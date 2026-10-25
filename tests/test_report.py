import pandas as pd
import pytest

from litmap import common, report, screening


def _log(name, *events):
  for event in events:
    common.append_log(name, event)


@pytest.fixture
def workspace(tmp_path, monkeypatch, settings):
  """A finished search, dedup and rule run, three screening decisions and one full-text export."""
  monkeypatch.setattr(common, "LOG_DIR", tmp_path)

  _log("search",
       {"event": "search", "run": "R1", "source": "pubmed", "records_saved": 4},
       {"event": "search", "run": "R1", "source": "openalex", "records_saved": 3})

  _log("chase", {"event": "round", "round": "C1", "new_works": {"W9": ["forward"]}})
  _log("dedup", {"event": "summary", "search_run": "R1", "records_in": 8, "duplicates_removed": 3})
  _log("screening", {"event": "rule_summary", "excluded": {"outside_year_range": 1}})
  _log("extraction", {"event": "export", "included": 1, "excluded": {"no_full_text": 1}})

  pool = pd.DataFrame({
    "title": ["A", "B", "C", "D"],
    "doi": [None] * 4,
    "pmid": [None] * 4,
  }, index=pd.Index(["r1", "r2", "r3", "r4"], name="record_id"))

  workspace = screening.Workspace.load(settings=settings, search_cfg={}, pool=pool, events=[])

  for label, reason in [("include", None), ("maybe", None), ("exclude", "not_running")]:
    workspace.decide(workspace.next_record_id(), label, reason=reason)

  return workspace


def test_prisma_counts_follow_every_record_from_search_to_inclusion(workspace):
  counts = report.prisma_counts(workspace)

  assert counts["databases"] == {"pubmed": 4, "openalex": 3}
  assert (counts["citation_chasing"], counts["identified_matches_dedup"]) == (1, True)
  assert (counts["screened"], counts["awaiting_screening"]) == (3, 1)
  assert counts["excluded_at_screening"] == {"not_running": 1}
  assert counts["sought_for_retrieval"] == 2  # include and maybe both go to the full text
  assert (counts["not_retrieved"], counts["assessed_for_eligibility"], counts["included"]) == (1, 1, 1)
  assert counts["awaiting_full_text"] == 0


def test_the_flow_diagram_shows_the_counts(workspace, monkeypatch, settings):
  monkeypatch.setattr(report, "load_screening_settings", lambda: settings)

  source = report.flow_diagram(report.prisma_counts(workspace)).source

  assert "Records identified from databases (n = 7)" in source
  assert "Records excluded (n = 1)" in source and "Not running (n = 1)" in source
  assert "Studies included in the review (n = 1)" in source
