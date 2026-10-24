"""Stage 3: screening, step 1: rule pre-filter.

Removes only clear negatives (high recall). Each rule is a named, versioned function that
returns True for the records it is certain about; missing data never excludes a record.
Every exclusion is logged to logs/screening.jsonl with the rule's name and version, and
every rule's flag is kept as a rule_<name> column, ready to be used as a ranker feature.

When you change a rule's logic, bump its version, so the log shows which logic made each decision.
"""

import pandas as pd

from litmap import common
from litmap.config import load_config, scope_limits

RULES = []


def rule(name, version):
  """Register a rule. Rules run in registration order; the first one that fires is the reason."""

  def register(rule_function):
    rule_function.rule_name = name
    rule_function.rule_version = version
    RULES.append(rule_function)

    return rule_function

  return register


@rule("language_not_allowed", version=1)
def language_not_allowed(records, cfg):
  """The language is known and not in search.yaml filters.languages."""
  allowed = scope_limits(cfg).languages

  if not allowed:
    return pd.Series(False, index=records.index)

  return records["language"].notna() & ~records["language"].isin(allowed)


@rule("outside_year_range", version=1)
def outside_year_range(records, cfg):
  """Every known date of the record is outside filters.year_from..year_to.

  Uses both the year and the first publication date, so a paper published online in
  2025 but printed in a 2026 issue is kept when year_to is 2025.
  """

  limits = scope_limits(cfg)

  known_years = pd.concat([  # plain floats: missing dates become NaN, which compares as False
    pd.to_numeric(records["year"], errors="coerce").astype(float),
    pd.to_numeric(records["first_published"].astype("string").str[:4], errors="coerce").astype(float),
  ], axis=1)

  earliest, latest = known_years.min(axis=1), known_years.max(axis=1)  # NaN when no date is known
  outside = pd.Series(False, index=records.index)

  if limits.year_to:
    outside |= earliest > limits.year_to

  if limits.year_from:
    outside |= latest < limits.year_from

  return outside


def apply_rules(records=None, cfg=None):
  """Flag every record with every rule and save the result for screening.

  Reads data/interim/records.parquet (unless `records` is given) and writes
  data/interim/prefiltered.parquet (see flag_records). Records with an empty excluded_by
  go on to screening. Logs every exclusion and a summary to logs/screening.jsonl.
  """

  in_path = common.INTERIM_DIR / "records.parquet"
  out_path = common.INTERIM_DIR / "prefiltered.parquet"
  from_file = records is None
  records = pd.read_parquet(in_path) if from_file else records
  cfg = cfg or load_config()

  flagged = flag_records(records, cfg)
  flagged.to_parquet(out_path, index=False)

  rules_run = common.run_id()
  versions = {rule_function.rule_name: rule_function.rule_version for rule_function in RULES}
  excluded = flagged[flagged["excluded_by"].notna()]
  _log_exclusions(rules_run, excluded, versions)

  summary = {
    "event": "rule_summary",
    "rules_run": rules_run,
    "logged_at": common.now(),
    "input_file": common.relative(in_path) if from_file else None,
    "input_sha256": common.sha256(in_path) if from_file else None,
    "output_file": common.relative(out_path),
    "output_sha256": common.sha256(out_path),
    "rules": versions,
    "records_in": len(flagged),
    "excluded": excluded["excluded_by"].value_counts().to_dict(),
    "to_screen": int(flagged["excluded_by"].isna().sum()),
  }

  common.append_log("screening", summary)
  _print_summary(summary)

  return flagged


def flag_records(records, cfg):
  """Run every rule: adds one rule_<name> column per rule, plus `excluded_by`.

  excluded_by is the first rule that fired, or empty if none did.
  """

  flagged = records.copy()
  flagged["excluded_by"] = pd.Series(pd.NA, index=records.index, dtype="string")

  for rule_function in RULES:
    fired = rule_function(records, cfg).fillna(False).astype(bool)
    not_yet_excluded = flagged["excluded_by"].isna()
    flagged[f"rule_{rule_function.rule_name}"] = fired
    flagged.loc[fired & not_yet_excluded, "excluded_by"] = rule_function.rule_name

  return flagged


def _log_exclusions(rules_run, excluded, versions):
  for record_id, rule_name in zip(excluded["record_id"], excluded["excluded_by"]):
    common.append_log("screening", {
      "event": "rule_exclusion",
      "rules_run": rules_run,
      "record_id": record_id,
      "rule": rule_name,
      "rule_version": versions[rule_name],
      "timestamp": common.now(),
    })


def _print_summary(summary):
  print(f"{summary['records_in']} records")

  for name, version in summary["rules"].items():
    print(f"  {name:22} v{version}: {summary['excluded'].get(name, 0)} excluded")

  print(f"{summary['to_screen']} to screen -> {summary['output_file']}")
