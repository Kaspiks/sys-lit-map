"""Record trail: everything the logs say about one record, stage by stage.

Read-only. It shows that every result traces back to a logged query or a logged decision,
and whether the files the logs fingerprint are still unchanged.

Run: python -m litmap.trail pubmed:26258007   (a record id, a merged record's id, a DOI or a PMID)
"""

import argparse
import json

import pandas as pd

from litmap import common, dedup, rules

RECORD_FIELDS = [
  "title", "journal", "year", "year_reported", "year_flag", "doi", "pmid", "pub_types", "databases",
  "excluded_by",
]


def find_record_id(query):
  """A record id from a record id, the id of a record merged into it, a DOI or a PMID (None if none)."""
  flagged = _flagged_records()
  query = query.strip()

  if query in flagged.index:
    return query

  members = flagged["member_ids"].str.split(dedup.MEMBER_SEPARATOR)

  matches = flagged.index[
    (flagged["doi"] == query.lower()) | (flagged["pmid"] == query) | members.map(lambda ids: query in ids)
  ]

  return matches[0] if len(matches) else None


def record_trail(record_id):
  """The trail of one record, ready for JSON. Raises KeyError for an unknown record id."""
  flagged = _flagged_records()
  record = flagged.loc[record_id]
  members = record["member_ids"].split(dedup.MEMBER_SEPARATOR)
  dedup_summary = _latest(common.read_log("dedup"), "summary")
  screening_events = common.read_log("screening")
  rule_summary = _latest(screening_events, "rule_summary")
  searches = _search_events(dedup_summary["search_run"])
  chased = _chased_works()

  return {
    "record_id": record_id,
    "record": {field: common.plain(record.get(field)) for field in RECORD_FIELDS},  # older runs lack some
    "search": [_found_by(member, searches, chased) for member in members],
    "dedup": _dedup_section(record, members, dedup_summary),
    "rules": _rules_section(record, rule_summary),
    "screening": [
      {"line": line, **event}
      for line, event in enumerate(screening_events, start=1)
      if event.get("record_id") == record_id
    ],
    "files": _file_checks(searches.values(), dedup_summary, rule_summary),
  }


def _flagged_records():
  return pd.read_parquet(common.INTERIM_DIR / "prefiltered.parquet").set_index("record_id")


def _latest(events, event_name):
  matching = [event for event in events if event.get("event") == event_name]

  if not matching:
    raise FileNotFoundError(f"No {event_name} event logged yet: run the earlier stages first")

  return matching[-1]


def _search_events(search_run):
  """{source: search event} for one search run. Early runs logged no event name."""

  return {
    event["source"]: event
    for event in common.read_log("search")
    if event.get("run") == search_run and event.get("event", "search") == "search"
  }


def _chased_works():
  """{member id: chase round event} for every work a citation-chasing round added."""

  return {
    f"openalex:{work_id}": event
    for event in common.read_log("chase")
    for work_id in event.get("new_works", {})
  }


def _found_by(member_id, searches, chased):
  database, source_id = member_id.split(":", 1)

  if member_id in chased:
    chase_round = chased[member_id]

    return {
      "member_id": member_id,
      "database": database,
      "source_id": source_id,
      "method": "chase",
      "run": chase_round["round"],
      "searched_at": chase_round["timestamp"],
      "directions": chase_round["new_works"][source_id],
    }

  search = searches.get(database, {})

  return {
    "member_id": member_id,
    "database": database,
    "source_id": source_id,
    "method": "search",
    "run": search.get("run"),
    "searched_at": search.get("searched_at"),
    "query": search.get("query"),
    "hit_count": search.get("hit_count"),
  }


def merges(dedup_run=None):
  """Every join of a deduplication run (default: the latest): which rule linked which two records."""
  dedup_run = dedup_run or _latest(common.read_log("dedup"), "summary")["dedup_run"]

  return [
    {key: event[key] for key in ("rule", "value", "record_a", "record_b")}
    for event in common.read_log("dedup")
    if event.get("event") == "merge" and event["dedup_run"] == dedup_run
  ]


def _dedup_section(record, members, dedup_summary):
  return {
    "dedup_run": dedup_summary["dedup_run"],
    "members": members,
    "merges": [
      merge for merge in merges(dedup_summary["dedup_run"])
      if merge["record_a"] in members or merge["record_b"] in members
    ],
    "field_sources": _field_sources(record, members, dedup_summary) if len(members) > 1 else {},
    "year_check": _year_check(record.name, dedup_summary["dedup_run"]),
  }


def _year_check(record_id, dedup_run):
  """What years.py logged about this record's year in this dedup run, or None if it wasn't flagged."""

  checks = [event for event in common.read_log("dedup") if event.get("event") == "year_check"]

  return next((
    check for check in checks if check["dedup_run"] == dedup_run and check["record_id"] == record_id
  ), None)


def _field_sources(record, members, dedup_summary):
  """Which member each merged field came from: the first, in priority order, with that value."""
  inputs = [dedup_summary["input_file"], *[chase["file"] for chase in dedup_summary.get("chase_files", [])]]
  stacked = pd.concat([common.read_records(common.ROOT / path) for path in inputs], ignore_index=True)
  stacked = stacked.assign(member_id=stacked["database"] + ":" + stacked["source_id"])
  rows = stacked[stacked["member_id"].isin(members)]
  rows = rows.assign(_priority=rows["database"].map(dedup.DATABASE_PRIORITY)).sort_values("_priority")
  sources = {}

  for field in dedup.MERGED_FIELDS:
    merged_value = common.plain(record[field])
    matching = rows[rows[field].map(lambda value: _same_value(field, value, merged_value))]
    sources[field] = matching["member_id"].iloc[0] if merged_value is not None and len(matching) else None

  return sources


def _same_value(field, value, merged_value):
  value = common.plain(value)

  if field == "doi":
    value = dedup.normalise_doi(value)

  return value is not None and str(value) == str(merged_value)


def _rules_section(record, rule_summary):
  descriptions = {rule.rule_name: rule.__doc__.splitlines()[0] for rule in rules.RULES}

  return {
    "rules_run": rule_summary["rules_run"],
    "excluded_by": common.plain(record["excluded_by"]),
    "rules": [
      {
        "rule": name,
        "version": version,
        "description": descriptions.get(name),
        "fired": bool(record[f"rule_{name}"]),
      }
      for name, version in rule_summary["rules"].items()
    ],
  }


def _file_checks(searches, dedup_summary, rule_summary):
  """Each file a log fingerprinted, and whether it still has the logged SHA-256."""
  logged = [(_raw_file(search), search.get("raw_sha256")) for search in searches]

  logged += [(chase["file"], chase["sha256"]) for chase in dedup_summary.get("chase_files", [])]

  logged += [
    (dedup_summary["input_file"], dedup_summary["input_sha256"]),
    (dedup_summary["output_file"], dedup_summary["output_sha256"]),
    (rule_summary["output_file"], rule_summary["output_sha256"]),
  ]

  return [_check_file(path, sha256) for path, sha256 in logged if path]


def _raw_file(search):
  """Runs from before file hashing didn't log the raw file's path; it follows the run layout."""

  return search.get("raw_file") or f"data/raw/{search['run']}/{search['source']}.json"


def _check_file(path, logged_sha256):
  full_path = common.ROOT / path

  if logged_sha256 is None:
    status = "not logged"
  elif not full_path.exists():
    status = "missing"
  elif common.sha256(full_path) == logged_sha256:
    status = "unchanged"
  else:
    status = "changed"

  return {"file": path, "sha256": logged_sha256, "status": status}


def _command_line():
  parser = argparse.ArgumentParser(description="Print everything the logs say about one record.")
  parser.add_argument("query", help="a record id, the id of a record merged into it, a DOI or a PMID")
  record_id = find_record_id(parser.parse_args().query)

  if record_id is None:
    raise SystemExit("No record with that id, DOI or PMID.")

  print(json.dumps(record_trail(record_id), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
  _command_line()
