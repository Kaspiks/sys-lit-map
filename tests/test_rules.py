import pandas as pd

from litmap import rules


def test_unknown_language_is_kept():
  records = pd.DataFrame({"language": ["en", "de", None]})
  cfg = {"filters": {"languages": ["en"]}}
  assert rules.language_not_allowed(records, cfg).tolist() == [False, True, False]


def test_year_rule_uses_the_earliest_known_date():
  records = pd.DataFrame({
    "year": pd.array([2026, 2026, None, 1985], dtype="Int64"),
    "first_published": ["2025-12-01", "2026-02-01", None, "1985-01-01"],
  })

  cfg = {"filters": {"year_from": 1990, "year_to": 2025}}
  # online in 2025 -> kept; only 2026 dates -> out; no date -> kept; 1985 -> out
  assert rules.outside_year_range(records, cfg).tolist() == [False, True, False, True]
