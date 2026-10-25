"""Stage 2: deduplication.

Records that are the same work are linked in this order:
  1. same DOI (normalised: lowercase, prefixes such as https://doi.org/ removed)
  2. same PMID
  3. same title after normalisation (title_key)
  4. fuzzy title match (token_sort_ratio >= FUZZY_THRESHOLD)
Steps 3 and 4 never join records with different DOIs, so in practice they only match
records without a DOI. Step 4 also requires years at most MAX_YEAR_GAP apart and the same
numbers in both titles ("Part 1" is not "Part 2").

The input is a search run's records.csv plus every citation-chasing round (chase.py), so
chased works merge with the ones the search found. Each group becomes one row with the richest
metadata, and every join is logged to logs/dedup.jsonl. Then years.py flags dissertations and
repository items whose year is probably a re-registration date, and corrects the ones related
works date (year_reported and year_flag columns, year_check events in the log).
Output: data/interim/records.parquet (and a .csv copy).
"""

import re
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process
from unidecode import unidecode

from litmap import common, years

FUZZY_THRESHOLD = 95  # in the pilot data, every pair below this was a different work (errata, datasets)
MAX_YEAR_GAP = 1  # online-first and print years can differ by one

# most curated metadata first: for each field, the merged row takes the first database that has it
DATABASE_PRIORITY = {"pubmed": 0, "europepmc": 1, "openalex": 2}

MERGED_FIELDS = [
  "title", "abstract", "year", "first_published", "journal", "authors",
  "doi", "pmid", "language", "pub_types",
]

OUTPUT_COLUMNS = ["record_id", *MERGED_FIELDS, "databases", "n_records", "member_ids"]
MEMBER_SEPARATOR = "; "


def deduplicate(run_dir=None, fuzzy_threshold=FUZZY_THRESHOLD):
  """Deduplicate a search run (default: the latest) with every chase round, and save the result.

  Writes data/interim/records.parquet (+ .csv) and appends one line per join, plus a
  summary with input/output hashes, to logs/dedup.jsonl. Returns the deduplicated table.
  """

  run_dir = Path(run_dir) if run_dir else common.latest_run()
  in_path = run_dir / "records.csv"
  chase_paths = [round_dir / "records.csv" for round_dir in common.chase_rounds()]
  out_path = common.INTERIM_DIR / "records.parquet"

  search_records = common.read_records(in_path)
  chase_records = [common.read_records(path) for path in chase_paths]
  df = add_keys(pd.concat([search_records, *chase_records], ignore_index=True))
  groups, joins, merges = find_groups(df, fuzzy_threshold)
  previous = pd.read_parquet(out_path) if out_path.exists() else None
  merged, retired = merge_groups(df, groups, previous)
  merged, year_checks = years.check_years(merged, years.run_year(run_dir.name))
  assert merged["record_id"].is_unique, "record ids must be unique"

  _save(merged, out_path)

  dedup_run = common.run_id()
  _log_merges(dedup_run, run_dir.name, _member_ids(df).tolist(), merges)
  _log_year_checks(dedup_run, year_checks)

  summary = {
    "event": "summary",
    "dedup_run": dedup_run,
    "search_run": run_dir.name,
    "logged_at": common.now(),
    "input_file": common.relative(in_path),
    "input_sha256": common.sha256(in_path),
    "chase_files": [{"file": common.relative(path), "sha256": common.sha256(path)} for path in chase_paths],
    "chase_records": len(df) - len(search_records),
    "output_file": common.relative(out_path),
    "output_sha256": common.sha256(out_path),
    "records_in": len(df),
    "records_out": len(merged),
    "duplicates_removed": len(df) - len(merged),
    "joins": joins,
    "fuzzy_threshold": fuzzy_threshold,
    "max_year_gap": MAX_YEAR_GAP,
    "retired_record_ids": retired,
    "year_flags": len(year_checks),
    "years_corrected": sum(check["source"] == "related_works" for check in year_checks),
  }

  common.append_log("dedup", summary)
  _print_summary(summary)

  return merged


def normalise_doi(doi):
  """'https://doi.org/10.1/ABC ' -> '10.1/abc'; anything that isn't a DOI -> None."""

  if not isinstance(doi, str):
    return None

  doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi.strip().lower())

  return doi if doi.startswith("10.") else None


def title_key(title):
  """Key for exact title matching: the same for titles that differ only in typography."""

  if not isinstance(title, str):
    return None

  return re.sub(r"[^a-z0-9]", "", unidecode(title).lower())


def fuzzy_text(title):
  """Like title_key, but keeps word boundaries, which token_sort_ratio needs."""

  if not isinstance(title, str):
    return ""

  return re.sub(r"[^a-z0-9]+", " ", unidecode(title).lower()).strip()


def add_keys(df):
  """Return a copy of df with normalised DOIs and the title columns used for matching."""

  return df.assign(
    doi=df["doi"].map(normalise_doi),
    title_key=df["title"].map(title_key),
    fuzzy_text=df["title"].map(fuzzy_text),
  )


class UnionFind:
  """Union-Find (disjoint set) data structure for grouping linked records.

  Each group also carries the set of DOIs of its rows (for the guard), and every join is
  recorded in self.merges as (row_a, row_b, rule, value) for the audit log.
  """

  def __init__(self, n, dois=None):
    self.parent = list(range(n))

    if dois is None:
      dois = [None] * n

    # one set per row: {"10.1/a"} if the row has a DOI, else an empty set
    self.dois = [{doi} if isinstance(doi, str) else set() for doi in dois]
    self.merges = []

  def find(self, row):
    root = row

    while self.parent[root] != root:
      root = self.parent[root]

    # path compression: point every row on the way straight at the root
    while row != root:
      next_row = self.parent[row]
      self.parent[row] = root
      row = next_row

    return root

  def union(self, a, b, guard=False, rule=None, value=None):
    root_a = self.find(a)
    root_b = self.find(b)

    if root_a == root_b:
      return False

    if guard and len(self.dois[root_a] | self.dois[root_b]) > 1:
      return False  # different DOIs = different works

    self.parent[root_b] = root_a
    self.dois[root_a] |= self.dois[root_b]
    self.merges.append((a, b, rule, value))

    return True


def find_groups(df, fuzzy_threshold=FUZZY_THRESHOLD):
  """Link records that are the same work. Returns (group per row, joins per rule, joins made)."""
  uf = UnionFind(len(df), dois=df["doi"])

  joins = {
    "doi": link_on(uf, df, "doi"),
    "pmid": link_on(uf, df, "pmid"),
    "title_key": link_on(uf, df, "title_key", guard=True),
    "fuzzy_title": link_fuzzy_titles(uf, df, threshold=fuzzy_threshold),
  }

  groups = [uf.find(row) for row in range(len(df))]

  return groups, joins, uf.merges


def link_on(uf, df, column, guard=False):
  """Link every row with the first earlier row that has the same value in `column`."""

  first_seen = {}
  joined = 0

  for row, value in enumerate(df[column]):
    if pd.isna(value):
      continue

    if value in first_seen:
      joined += uf.union(row, first_seen[value], guard=guard, rule=column, value=value)
    else:
      first_seen[value] = row

  return joined


def link_fuzzy_titles(uf, df, threshold=FUZZY_THRESHOLD, max_year_gap=MAX_YEAR_GAP):
  """Link records whose titles are near-identical, always with the DOI guard."""
  texts = df["fuzzy_text"].tolist()
  years = df["year"].tolist()
  numbers = [re.findall(r"\d+", text) for text in texts]
  joined = 0

  for score, a, b in _similar_title_pairs(texts, threshold):
    if _could_be_same_work(years[a], years[b], numbers[a], numbers[b], max_year_gap):
      joined += uf.union(a, b, guard=True, rule="fuzzy_title", value=round(float(score), 1))

  return joined


def merge_groups(df, groups, previous=None):
  """One row per work. Returns (merged table, list of retired record ids).

  Each field comes from the highest-priority database that has it, except the abstract,
  which is the longest in the group. record_id is the highest-priority member's id
  ("pubmed:123"), unless the work already had an id in `previous` (the last dedup output):
  then that id is kept, so screening decisions stay attached when dedup reruns.
  """

  df = df.assign(
    group=groups,
    member_id=_member_ids(df),
    _priority=df["database"].map(DATABASE_PRIORITY),
  ).sort_values(["group", "_priority"], kind="stable")

  by_group = df.groupby("group", sort=False)

  merged = by_group[MERGED_FIELDS].first()  # first non-missing value per column, in priority order
  merged["abstract"] = _longest_abstracts(df)
  merged["databases"] = by_group["database"].agg(lambda names: "+".join(sorted(set(names))))
  merged["n_records"] = by_group.size()
  merged["member_ids"] = by_group["member_id"].agg(MEMBER_SEPARATOR.join)
  merged["record_id"] = by_group["member_id"].first()
  merged, retired = _keep_previous_ids(merged, previous)

  return merged[OUTPUT_COLUMNS].reset_index(drop=True), retired


def _similar_title_pairs(texts, threshold):
  """Every pair of rows whose titles score >= threshold, best first, so row order doesn't matter.

  Compares all titles at once (rapidfuzz cdist), which is fine for thousands of records.
  """

  scores = process.cdist(texts, texts, scorer=fuzz.token_sort_ratio, score_cutoff=threshold, workers=-1)
  pairs = [(scores[a, b], a, b) for a, b in zip(*scores.nonzero()) if a < b and texts[a] and texts[b]]

  return sorted(pairs, key=lambda pair: (-pair[0], pair[1], pair[2]))


def _could_be_same_work(year_a, year_b, numbers_a, numbers_b, max_year_gap):
  """Similar titles are still different works if the years are far apart or the numbers differ."""
  both_years_known = pd.notna(year_a) and pd.notna(year_b)

  if both_years_known and abs(year_a - year_b) > max_year_gap:
    return False

  return numbers_a == numbers_b


def _member_ids(df):
  """'pubmed:26258007', 'europepmc:MED:26258007', 'openalex:W1839056034'."""

  return df["database"] + ":" + df["source_id"].astype(str)


def _longest_abstracts(df):
  """The richest abstract of each group: the longest one (ties go to the higher priority)."""
  # as text even when every abstract is missing, which pandas would otherwise store as numbers
  by_length = df.assign(_length=df["abstract"].astype("string").str.len().fillna(0))
  by_length = by_length.sort_values(["group", "_length"], ascending=[True, False], kind="stable")

  return by_length.groupby("group", sort=False)["abstract"].first()


def _keep_previous_ids(merged, previous):
  """Reuse record ids from the previous dedup output for works it already contained.

  If two previously separate works are now one group, the first id (alphabetically) is
  kept and the other is returned as retired, so screening decisions on it can be moved.
  """

  if previous is None or previous.empty:
    return merged, []

  previous_id_of = {
    member: record_id
    for record_id, members in zip(previous["record_id"], previous["member_ids"])
    for member in members.split(MEMBER_SEPARATOR)
  }

  ids, used, retired = [], set(), []

  for canonical_id, members in zip(merged["record_id"], merged["member_ids"]):
    known_members = [member for member in members.split(MEMBER_SEPARATOR) if member in previous_id_of]
    known_ids = {previous_id_of[member] for member in known_members}
    old_ids = sorted(known_ids - used)
    record_id = old_ids[0] if old_ids else canonical_id
    retired += [{"retired": old_id, "kept": record_id} for old_id in old_ids[1:]]
    used.add(record_id)
    ids.append(record_id)

  return merged.assign(record_id=ids), retired


def _save(merged, out_path):
  common.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
  merged.to_parquet(out_path, index=False)
  merged.to_csv(out_path.with_suffix(".csv"), index=False)  # the same table, for opening in Excel


def _log_merges(dedup_run, search_run, member_ids, merges):
  for a, b, rule, value in merges:
    common.append_log("dedup", {
      "event": "merge",
      "dedup_run": dedup_run,
      "search_run": search_run,
      "rule": rule,
      "value": value,
      "record_a": member_ids[a],
      "record_b": member_ids[b],
    })


def _log_year_checks(dedup_run, year_checks):
  for check in year_checks:
    common.append_log("dedup", {"event": "year_check", "dedup_run": dedup_run, **check})


def _print_summary(summary):
  joins = ", ".join(f"{rule} {count}" for rule, count in summary["joins"].items())
  chased = f", {summary['chase_records']} of them from citation chasing" if summary["chase_records"] else ""
  print(f"search run {summary['search_run']}: {summary['records_in']} records{chased}")
  print(f"joins: {joins}")

  print(
    f"{summary['records_in']} identified -> {summary['duplicates_removed']} duplicates removed"
    f" -> {summary['records_out']} records -> {summary['output_file']}"
  )

  if summary["year_flags"]:
    print(f"years: {summary['year_flags']} flagged as doubtful, {summary['years_corrected']} corrected"
          " from related works, see the year_check events in logs/dedup.jsonl")

  if summary["retired_record_ids"]:
    retired = len(summary["retired_record_ids"])
    print(f"WARNING: {retired} previously separate records are now merged, see logs/dedup.jsonl")
