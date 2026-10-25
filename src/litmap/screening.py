"""Stage 3: screening, step 3: a person decides every record.

Every decision and every undo is an event appended to logs/screening.jsonl. The current
state is rebuilt by replaying those events, so the log is the only source of truth: the
ranker, the PRISMA report and replay.py all read the same events.

Records come from the rule pre-filter: rows of data/interim/prefiltered.parquet with an
empty excluded_by. The seed set comes first, in a fixed random order, because its labels
train the ranker and must not be biased by any ranking. After it, the latest ranker
iteration (rank.py) decides the order, and the ranker retrains after every batch of decisions.

A second screener (any initials other than screening.yaml's screener) screens blind: a fixed
random sample of the pool, without the ranker's order and without seeing your decisions.
agreement.py then compares the two.
"""

import random
import uuid
from dataclasses import dataclass, field
from typing import NamedTuple

import pandas as pd

from litmap import common
from litmap.config import load_config, load_screening_settings

STAGE = "title_abstract"
LABELS = ("include", "maybe", "exclude")
RELEVANT_LABELS = ("include", "maybe")
DEFAULT_BATCH_SIZE = 25

SEED_SET_QUEUE = "random_seed_set"
RANKED_QUEUE = "ranker"
UNRANKED_QUEUE = "random"
SECOND_SCREENER_QUEUE = "second_screener_sample"


class Ranking(NamedTuple):
  """The latest ranker iteration, as the screening order uses it."""

  iteration: int
  trained_on: int  # decisions in its training set
  order: list  # record ids, most likely relevant first
  scores: dict  # record id -> score; logged with each decision, never shown


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
  ranking: Ranking | None = None
  screener: str = ""  # whose view this is; empty means screening.yaml's screener
  events: list = field(default_factory=list)  # this screener's decisions and undos, oldest first

  @classmethod
  def load(cls, settings=None, search_cfg=None, pool=None, events=None, screener=None):
    settings = settings or load_screening_settings()
    search_cfg = search_cfg or load_config()
    pool = load_pool() if pool is None else pool
    events = common.read_log("screening") if events is None else events
    screener = screener or settings.screener
    validation = search_cfg.get("validation") or []
    key_ids = validation_record_ids(pool, validation)

    if screener == settings.screener:
      ranking = latest_ranking(events)
      random_order = queue_order(pool.index, key_ids, settings.seed, settings.seed_set_size)
      order = screening_order(random_order, settings.seed_set_size, ranking)
    else:
      ranking = None
      order = second_screener_sample(pool.index, settings.seed, settings.second_sample_size)

    return cls(
      settings=settings,
      pool=pool,
      order=order,
      key_ids=key_ids,
      key_total=len(validation),
      decisions=replay_decisions(events, screener),
      ranking=ranking,
      screener=screener,
      events=[event for event in events if event.get("stage") == STAGE and event.get("screener") == screener],
    )

  @property
  def blind(self):
    """True for a second screener: a random sample, no ranker, nobody else's decisions."""

    return bool(self.screener) and self.screener != self.settings.screener

  def next_record_id(self):
    return next((record_id for record_id in self.order if record_id not in self.decisions), None)

  def progress(self):
    in_a_row = irrelevant_in_a_row(self.decisions)
    key_found = sum(self._label_of(record_id) in RELEVANT_LABELS for record_id in self.key_ids)

    seconds = [decision["seconds_on_record"] for decision in self.decisions.values()
               if decision.get("seconds_on_record") is not None]

    seed_set = self.order[:self.settings.seed_set_size]

    return {
      "screener": self.screener or self.settings.screener,
      "blind": self.blind,
      "screened": len(self.decisions),
      "pool": len(self.order) if self.blind else len(self.pool),
      "irrelevant_in_a_row": in_a_row,
      "stop_after": self.settings.stop_after_irrelevant,
      "key_studies_found": key_found,
      "key_studies_total": self.key_total,
      "key_studies_in_pool": len(self.key_ids),
      "seed_set_screened": sum(record_id in self.decisions for record_id in seed_set),
      "seed_set_size": len(seed_set),
      "seed_set_done": self.seed_set_done(),
      "ranker_iteration": self.ranking.iteration if self.ranking else None,
      "retrain_in": self._retrain_in(),
      "average_seconds": round(sum(seconds) / len(seconds)) if seconds else None,
      "stopping_rule_met": in_a_row >= self.settings.stop_after_irrelevant and key_found == self.key_total,
    }

  def seed_set_done(self):
    return all(record_id in self.decisions for record_id in self.order[:self.settings.seed_set_size])

  def ranker_due(self):
    """True when the ranker should retrain: the seed set is done and a batch of new decisions is in."""

    return self._retrain_in() == 0

  def feed(self, count=8):
    """This screener's latest decision and undo events, newest first, numbered in log order."""
    numbered = [{"number": number, **event} for number, event in enumerate(self.events, start=1)]

    return numbered[::-1][:count]

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
      "screener": self.screener or self.settings.screener,
      **self.queue_details(record_id),
      "queue_position": self.order.index(record_id) + 1,
      "seconds_on_record": seconds,
      "timestamp": common.now(),
    }

    common.append_log("screening", event)
    self.decisions[record_id] = event
    self.events.append(event)

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
      "screener": self.screener or self.settings.screener,
      "timestamp": common.now(),
    }

    common.append_log("screening", event)
    del self.decisions[record_id]
    self.events.append(event)

    return event

  def _label_of(self, record_id):
    return self.decisions.get(record_id, {}).get("label")

  def _retrain_in(self):
    """Decisions until the ranker retrains (0 = due now), or None during the seed set."""

    if not self.seed_set_done():
      return None

    if self.ranking is None:
      return 0

    batch_size = self.settings.ranker.get("batch_size", DEFAULT_BATCH_SIZE)
    new_decisions = len(self.decisions) - self.ranking.trained_on

    return max(0, batch_size - new_decisions)

  def queue_details(self, record_id):
    """Which queue the record comes from and, for the ranker's queue, its rank and score (logged only)."""

    if self.blind:
      return {"queue": SECOND_SCREENER_QUEUE}

    if record_id in self.order[:self.settings.seed_set_size]:
      return {"queue": SEED_SET_QUEUE}

    if self.ranking is None or record_id not in self.ranking.scores:
      return {"queue": UNRANKED_QUEUE}

    return {
      "queue": RANKED_QUEUE,
      "ranker_iteration": self.ranking.iteration,
      "rank": self.ranking.order.index(record_id) + 1,
      "score": round(float(self.ranking.scores[record_id]), 6),
    }


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


def second_screener_sample(record_ids, seed, size):
  """The blind second screener's records: a fixed random sample of the pool, in random order."""
  rng = random.Random(f"{seed}-second-screener")
  record_ids = sorted(record_ids)

  return rng.sample(record_ids, min(size, len(record_ids)))


def record_statuses(workspace=None):
  """Every deduplicated record and where it stands, for the citation registry.

  status is excluded_by_rule, include, maybe, exclude (your decisions) or to_screen.
  """

  workspace = workspace or Workspace.load()
  flagged = pd.read_parquet(common.INTERIM_DIR / "prefiltered.parquet").set_index("record_id")
  rows = []

  for record_id, record in flagged.iterrows():
    decision = workspace.decisions.get(record_id)

    if common.plain(record["excluded_by"]):
      status, detail = "excluded_by_rule", record["excluded_by"]
    elif decision:
      status, detail = decision["label"], decision.get("reason")
    else:
      status, detail = "to_screen", None

    rows.append({
      "record_id": record_id,
      "title": common.plain(record["title"]),
      "year": common.plain(record["year"]),
      "databases": record["databases"],
      "status": status,
      "detail": detail,
    })

  return rows


def latest_ranking(events):
  rank_events = [event for event in events if event.get("event") == "rank"]

  if not rank_events:
    return None

  event = rank_events[-1]
  ranking = pd.read_parquet(common.ROOT / event["ranking_file"])

  return Ranking(
    iteration=event["iteration"],
    trained_on=len(event["training"]),
    order=list(ranking["record_id"]),
    scores=dict(zip(ranking["record_id"], ranking["score"])),
  )


def screening_order(random_order, seed_set_size, ranking):
  """The seed set in its random order, then the other records by the latest ranking.

  Records the ranking doesn't cover (there is no ranking yet, or it predates them) follow
  in their random order.
  """

  if ranking is None:
    return random_order

  seed_set, rest = random_order[:seed_set_size], random_order[seed_set_size:]
  remaining = set(rest)
  ranked = [record_id for record_id in ranking.order if record_id in remaining]
  ranked_set = set(ranked)

  return seed_set + ranked + [record_id for record_id in rest if record_id not in ranked_set]


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
