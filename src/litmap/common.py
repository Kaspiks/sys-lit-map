"""Paths, run ids, logging and the common record table, shared by every stage.

Stages refer to these as common.RAW_DIR etc. at call time, so a test can point them elsewhere.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
RAW_DIR = ROOT / "data" / "raw"
INTERIM_DIR = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUTS_DIR = ROOT / "outputs"
LOG_DIR = ROOT / "logs"
MODELS_DIR = ROOT / "models"

# every source adapter flattens its records into this table, so dedup can merge them
RECORD_COLUMNS = [
  "database", "source_id", "pmid", "doi", "title", "abstract", "year",
  "first_published", "journal", "authors", "language", "pub_types",
]


def records_frame(rows):
  """Build the common record table from one dict per record."""

  return pd.DataFrame(list(rows), columns=RECORD_COLUMNS).astype({"year": "Int64"})


def read_records(path):
  """A search run's records.csv, with the column types the record table was written with."""

  return pd.read_csv(path, dtype={"pmid": str, "source_id": str, "year": "Int64"})


def now():
  return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_id():
  """Names one run of a stage, e.g. '20261024T121335Z' (UTC, so names sort by time)."""

  return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def relative(path):
  """A path as it appears in the logs: relative to the project root."""

  return str(Path(path).relative_to(ROOT))


def sha256(path):
  """Fingerprint of a file, logged so every result can be traced to its exact inputs."""
  digest = hashlib.sha256()

  with open(path, "rb") as file:
    for chunk in iter(lambda: file.read(1 << 20), b""):
      digest.update(chunk)

  return digest.hexdigest()


def append_log(name, entry):
  """Append one JSON line to logs/<name>.jsonl. Logs are append-only: never edit old lines."""
  LOG_DIR.mkdir(parents=True, exist_ok=True)

  with open(LOG_DIR / f"{name}.jsonl", "a") as log:
    log.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def read_log(name):
  """All events in logs/<name>.jsonl, oldest first (empty if the log doesn't exist yet)."""
  path = LOG_DIR / f"{name}.jsonl"

  if not path.exists():
    return []

  with open(path) as log:
    return [json.loads(line) for line in log if line.strip()]


def plain(value):
  """A pandas or numpy value as plain Python for JSON; missing values become None."""

  if isinstance(value, (str, list, dict)):
    return value

  if value is None or pd.isna(value):
    return None

  return value.item() if hasattr(value, "item") else value


def chase_rounds():
  """Citation-chasing rounds in data/raw/chase/, oldest first: folders holding a records.csv."""
  chase_dir = RAW_DIR / "chase"
  rounds = chase_dir.iterdir() if chase_dir.exists() else []

  return sorted(round_dir for round_dir in rounds if (round_dir / "records.csv").exists())


def latest_run():
  """The newest complete search run in data/raw/ (a failed run has no records.csv)."""
  runs = RAW_DIR.iterdir() if RAW_DIR.exists() else []
  complete_runs = sorted(run for run in runs if (run / "records.csv").exists())

  if not complete_runs:
    raise FileNotFoundError(f"No search runs in {RAW_DIR}: run search.run_search() first")

  return complete_runs[-1]
