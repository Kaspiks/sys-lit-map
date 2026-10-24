"""Stage 1: automated search.

Runs the search in config/search.yaml on every source listed under `sources`, saves the raw
responses to data/raw/<run>/ and logs every query (source, exact query, date, hit count).
The database-specific work lives in litmap/sources/, one adapter per database.

High recall: by default no year or language limits go into the queries. rules.py applies
them later as named, logged rules, and keeps records whose language or year is unknown.
"""

import json

import pandas as pd
import yaml

from litmap import common
from litmap.config import load_config
from litmap.sources import SOURCES


def run_search(cfg=None, filters=False):
  """Run the config's search on every source and save everything needed to reproduce it.

  Writes data/raw/<run>/ with config.yaml, one raw <source>.json per database and
  records.csv (all databases stacked, duplicates included: dedup is stage 2). Appends one
  line per database, plus the validation result, to logs/search.jsonl. Returns the stacked table.

  filters=False (default) keeps every year and language, see the module docstring.
  """

  cfg = cfg or load_config()
  run_dir = common.RAW_DIR / common.run_id()
  run_dir.mkdir(parents=True)
  config_sha256 = _save_config(cfg, run_dir)

  frames = [_search_source(name, cfg, filters, run_dir, config_sha256) for name in cfg["sources"]]
  records = pd.concat(frames, ignore_index=True)
  records_path = run_dir / "records.csv"
  records.to_csv(records_path, index=False)

  validation = check_validation(cfg, records)
  _log_validation(validation, run_dir, records_path)
  _print_totals(records, records_path, validation)

  return records


def check_validation(cfg, records):
  """Which known key studies (search.yaml `validation`) did the search retrieve?

  Matched by DOI or PMID, never by author and year: databases disagree on years
  (OpenAlex dates Van Dyck 2021 as 2020, its online-first year).
  """

  found_dois = set(records["doi"].dropna().str.lower())
  found_pmids = set(records["pmid"].dropna().astype(str))
  expected = cfg.get("validation") or []
  missing = [study for study in expected if not _was_retrieved(study, found_dois, found_pmids)]

  return {"expected": len(expected), "found": len(expected) - len(missing), "missing": missing}


def _was_retrieved(study, found_dois, found_pmids):
  return str(study.get("doi", "")).lower() in found_dois or str(study.get("pmid", "")) in found_pmids


def _save_config(cfg, run_dir):
  """Keep a copy of the config next to its results, so later edits can't rewrite history."""
  config_path = run_dir / "config.yaml"
  config_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))

  return common.sha256(config_path)


def _search_source(name, cfg, filters, run_dir, config_sha256):
  """Search one source, save its raw response and log the query. Returns its record table."""
  source = SOURCES[name]
  hit_count, raw_records = source.search(cfg, filters=filters)
  raw_path = run_dir / f"{name}.json"
  raw_path.write_text(json.dumps(raw_records, ensure_ascii=False))
  records = source.to_frame(raw_records)

  common.append_log("search", {
    "event": "search",
    "run": run_dir.name,
    "searched_at": common.now(),
    "source": name,
    "query": source.make_query(cfg, filters=filters),  # exactly what was sent, minus API keys
    "filters_in_query": filters,
    "hit_count": hit_count,
    "records_saved": len(records),
    "config_sha256": config_sha256,
    "raw_file": common.relative(raw_path),
    "raw_sha256": common.sha256(raw_path),
  })

  missing = hit_count - len(records)
  warning = f"  <- WARNING: {missing} records missing" if missing else ""
  print(f"{name:10} {hit_count:>5} hits, {len(records):>5} saved{warning}")

  return records


def _log_validation(validation, run_dir, records_path):
  common.append_log("search", {
    "event": "validation",
    "run": run_dir.name,
    "records_file": common.relative(records_path),
    "records_sha256": common.sha256(records_path),
    **validation,
  })


def _print_totals(records, records_path, validation):
  print(f"{'total':10} {len(records):>5} records -> {common.relative(records_path)}")

  missing = ", ".join(study.get("label", "?") for study in validation["missing"])
  warning = f"  <- MISSING: {missing}" if missing else ""
  print(f"validation: {validation['found']}/{validation['expected']} known studies retrieved{warning}")
