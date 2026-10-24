"""Stage 3: screening, step 3: a person decides every record.

Every decision and every undo is an event appended to logs/screening.jsonl. The current
state is rebuilt by replaying those events, so the log is the only source of truth: the
ranker, the PRISMA report and replay.py all read the same events.

Records come from the rule pre-filter: rows of data/interim/prefiltered.parquet with an
empty excluded_by. Until the ranker exists they are shown in a fixed random order, because
the seed set's labels will train the ranker and must not be biased by any ranking.
"""

import random
import uuid
from dataclasses import dataclass

import pandas as pd

from litmap import common
from litmap.config import load_config, load_screening_settings

STAGE = "title_abstract"
LABELS = ("include", "maybe", "exclude")
RELEVANT_LABELS = ("include", "maybe")
QUEUE = "random_seed_set"  # which ordering showed the record; the ranker will log its own


class DecisionError(ValueError):
  """A decision or undo that can't be recorded as given."""


class StaleRecordError(DecisionError):
  """The decision is for a record that isn't due next (for example from a second open tab)."""


@dataclass
class Workspace:
  """One screener's view of the screening pool, rebuilt from the log on every load."""

  settings: object
  pool: pd.DataFrame  # records to screen, indexed by record_id
  order: list
  key_ids: set  # validation studies in the pool; never shown as such, only counted
  key_total: int
  decisions: dict  # record_id -> decision still in force, oldest first

  @classmethod
  def load(cls, settings=None, search_cfg=None, pool=None, events=None):
    settings = settings or load_screening_settings()
    search_cfg = search_cfg or load_config()
    pool = load_pool() if pool is None else pool
    events = common.read_log("screening") if events is None else events
    validation = search_cfg.get("validation") or []
    key_ids = validation_record_ids(pool, validation)

    return cls(
      settings=settings,
      pool=pool,
      order=queue_order(pool.index, key_ids, settings.seed, settings.seed_set_size),
      key_ids=key_ids,
      key_total=len(validation),
      decisions=replay_decisions(events, settings.screener),
    )

  def next_record_id(self):
    return next((record_id for record_id in self.order if record_id not in self.decisions), None)

  def progress(self):
    in_a_row = irrelevant_in_a_row(self.decisions)
    key_found = sum(self._label_of(record_id) in RELEVANT_LABELS for record_id in self.key_ids)

    seconds = [decision["seconds_on_record"] for decision in self.decisions.values()
               if decision.get("seconds_on_record") is not None]

    seed_set = self.order[:self.settings.seed_set_size]

    return {
      "screened": len(self.decisions),
      "pool": len(self.pool),
      "irrelevant_in_a_row": in_a_row,
      "stop_after": self.settings.stop_after_irrelevant,
      "key_studies_found": key_found,
      "key_studies_total": self.key_total,
      "key_studies_in_pool": len(self.key_ids),
      "seed_set_screened": sum(record_id in self.decisions for record_id in seed_set),
      "seed_set_size": len(seed_set),
      "average_seconds": round(sum(seconds) / len(seconds)) if seconds else None,
      "stopping_rule_met": in_a_row >= self.settings.stop_after_irrelevant and key_found == self.key_total,
    }

  def recent(self, count=5):
    """The latest decisions still in force, newest first."""

    return list(self.decisions.values())[::-1][:count]

  def decide(self, record_id, label, reason=None, note=None, seconds=None):
    """Record a decision on the record that is due next. Returns the logged event."""

    if record_id != self.next_record_id():
      raise StaleRecordError("That record was already decided. Showing the next one.")

    _check_decision(self.settings, label, reason, note)

    event = {
      "event": "decision",
      "stage": STAGE,
      "decision_id": uuid.uuid4().hex,
      "record_id": record_id,
      "label": label,
      "reason": reason if label == "exclude" else None,
      "note": note or None,
      "screener": self.settings.screener,
      "queue": QUEUE,
      "queue_position": self.order.index(record_id) + 1,
      "seconds_on_record": seconds,
      "timestamp": common.now(),
    }

    common.append_log("screening", event)
    self.decisions[record_id] = event

    return event

  def undo_last(self):
    """Revert the latest decision still in force. Returns the logged undo event."""

    if not self.decisions:
      raise DecisionError("Nothing to undo.")

    record_id, decision = list(self.decisions.items())[-1]

    event = {
      "event": "undo",
      "stage": STAGE,
      "undoes": decision["decision_id"],
      "record_id": record_id,
      "screener": self.settings.screener,
      "timestamp": common.now(),
    }

    common.append_log("screening", event)
    del self.decisions[record_id]

    return event

  def _label_of(self, record_id):
    return self.decisions.get(record_id, {}).get("label")


def load_pool():
  """Records that passed the rule pre-filter, indexed by record_id."""
  flagged = pd.read_parquet(common.INTERIM_DIR / "prefiltered.parquet")

  return flagged[flagged["excluded_by"].isna()].set_index("record_id")


def replay_decisions(events, screener, stage=STAGE):
  """Replay decision and undo events: {record_id: decision still in force}, oldest first."""
  in_force = {}  # decision_id -> decision

  for event in events:
    if event.get("stage") != stage or event.get("screener") != screener:
      continue

    if event["event"] == "decision":
      in_force[event["decision_id"]] = event
    elif event["event"] == "undo":
      in_force.pop(event["undoes"], None)

  return {decision["record_id"]: decision for decision in in_force.values()}


def irrelevant_in_a_row(decisions):
  """Excludes since the last include or maybe (decisions oldest first)."""
  count = 0

  for decision in reversed(decisions.values()):
    if decision["label"] != "exclude":
      break

    count += 1

  return count


def queue_order(record_ids, key_ids, seed, seed_set_size):
  """A fixed random order, with the key studies at random places inside the seed set.

  Random, so the seed set's labels aren't biased by any ranking; key studies inside the
  seed set, so the ranker learns from them; at random places, so they can't be recognised.
  """

  rng = random.Random(seed)
  order = sorted(set(record_ids) - set(key_ids))
  rng.shuffle(order)
  seed_set_end = min(seed_set_size, len(order))

  for key_id in sorted(key_ids):
    order.insert(rng.randint(0, seed_set_end), key_id)

  return order


def validation_record_ids(pool, validation):
  """Record ids of the validation studies (search.yaml, matched by DOI or PMID) in the pool."""
  dois = pool["doi"].str.lower()
  record_ids = set()

  for study in validation:
    same_doi = dois == str(study.get("doi", "")).lower()
    same_pmid = pool["pmid"] == str(study.get("pmid", ""))
    record_ids.update(pool.index[same_doi | same_pmid])

  return record_ids


def _check_decision(settings, label, reason, note):
  if label not in LABELS:
    raise DecisionError(f"Unknown decision {label!r}; use one of {', '.join(LABELS)}.")

  if label != "exclude":
    return

  exclusion_reason = settings.reason(reason)

  if exclusion_reason is None:
    raise DecisionError("Choose a reason for excluding this record.")

  if exclusion_reason.note_required and not (note or "").strip():
    raise DecisionError(f"Add a note for “{exclusion_reason.label}”.")
