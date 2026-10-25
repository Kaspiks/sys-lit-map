"""Agreement between you and a blind second screener (the plan's kappa check).

The second screener screens a fixed random sample with their own initials (screening.py).
On the records you both decided, agreement is measured on the decision that matters for the
review: relevant (include or maybe) or not (exclude). Cohen's kappa corrects the raw agreement
for the agreement two screeners would reach by chance.
"""

from sklearn.metrics import cohen_kappa_score

from litmap import common, screening
from litmap.config import load_screening_settings

RELEVANT, NOT_RELEVANT = "relevant", "not relevant"


def second_screeners(events=None, settings=None):
  """Initials of everyone except you who has logged screening decisions."""
  settings = settings or load_screening_settings()
  events = common.read_log("screening") if events is None else events

  return sorted({
    event["screener"] for event in events
    if event.get("event") == "decision" and event.get("screener") not in (None, settings.screener)
  })


def agreement(second, events=None, settings=None):
  """How far you and `second` agree on the records you both decided, ready for JSON."""
  settings = settings or load_screening_settings()
  events = common.read_log("screening") if events is None else events
  yours = screening.replay_decisions(events, settings.screener)
  theirs = screening.replay_decisions(events, second)
  both = sorted(set(yours) & set(theirs))
  your_labels = [yours[record_id]["label"] for record_id in both]
  their_labels = [theirs[record_id]["label"] for record_id in both]
  your_relevance = [_relevance(label) for label in your_labels]
  their_relevance = [_relevance(label) for label in their_labels]
  agreeing = sum(a == b for a, b in zip(your_relevance, their_relevance))

  return {
    "primary": settings.screener,
    "second": second,
    "sample_size": settings.second_sample_size,
    "screened_by_second": len(theirs),
    "screened_by_both": len(both),
    "agreement": round(agreeing / len(both), 3) if both else None,
    "kappa": kappa(your_relevance, their_relevance),
    "table": _label_table(your_labels, their_labels),
    "disagreements": [
      {"record_id": record_id, "primary": yours[record_id]["label"], "second": theirs[record_id]["label"]}
      for record_id, a, b in zip(both, your_relevance, their_relevance)
      if a != b
    ],
  }


def kappa(first, second):
  """Cohen's kappa, or None when it's undefined (fewer than two records, or one category only)."""

  if len(first) < 2 or len(set(first) | set(second)) < 2:
    return None

  return round(float(cohen_kappa_score(first, second)), 3)


def _relevance(label):
  return RELEVANT if label in screening.RELEVANT_LABELS else NOT_RELEVANT


def _label_table(your_labels, their_labels):
  """{your label: {their label: count}} over include, maybe and exclude."""
  table = {mine: dict.fromkeys(screening.LABELS, 0) for mine in screening.LABELS}

  for mine, theirs in zip(your_labels, their_labels):
    table[mine][theirs] += 1

  return table
