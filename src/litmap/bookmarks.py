import pandas as pd

from litmap import common

LOG = "bookmarks"
CITATION_FIELDS = ("title", "year", "journal", "doi")


def bookmarks(screener, events=None):
  """{record_id: latest bookmark event} of one screener's current bookmarks, oldest first."""
  events = common.read_log(LOG) if events is None else events
  current = {}

  for event in events:
    if event.get("screener") != screener:
      continue

    if event["event"] == "bookmark":
      current[event["record_id"]] = event
    elif event["event"] == "remove":
      current.pop(event["record_id"], None)

  return current


def add(record_id, screener, note=None, records=None):
  """Bookmark a record, or change its note. Raises KeyError for an unknown record id."""
  records = _records() if records is None else records
  record = records.loc[record_id]

  event = {
    "event": "bookmark",
    "record_id": record_id,
    "screener": screener,
    "note": (note or "").strip() or None,
    **{field: common.plain(record.get(field)) for field in CITATION_FIELDS},
    "timestamp": common.now(),
  }

  common.append_log(LOG, event)

  return event


def remove(record_id, screener):
  event = {"event": "remove", "record_id": record_id, "screener": screener, "timestamp": common.now()}
  common.append_log(LOG, event)

  return event


def _records():
  """Every deduplicated record, rule-excluded ones too: any of them can be bookmarked from its trail."""

  return pd.read_parquet(common.INTERIM_DIR / "prefiltered.parquet").set_index("record_id")
