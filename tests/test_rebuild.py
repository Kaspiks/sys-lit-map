import pandas as pd
import pytest
import yaml

from litmap import common, screening
from litmap.ui.rebuild import SearchRebuild


@pytest.fixture
def previous_run(tmp_path, monkeypatch):
  monkeypatch.setattr(common, "ROOT", tmp_path)
  for name, relative in {
    "CONFIG_DIR": "config", "RAW_DIR": "data/raw", "INTERIM_DIR": "data/interim",
    "PROCESSED_DIR": "data/processed", "MODELS_DIR": "models",
    "OUTPUTS_DIR": "outputs", "LOG_DIR": "logs",
  }.items():
    path = tmp_path / relative
    path.mkdir(parents=True)
    monkeypatch.setattr(common, name, path)

  files = {
    "config/screening.yaml": "seed: 1",
    "data/raw/old/records.csv": "old search",
    "data/raw/chase/old/records.csv": "old chased records",
    "data/interim/records.csv": "old pool",
    "data/processed/extraction.xlsx": "old extraction",
    "models/iteration_001.joblib": "old model",
    "outputs/report.md": "old report",
  }
  for relative, text in files.items():
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

  ranking_path = common.INTERIM_DIR / "rankings" / "iteration_001.parquet"
  ranking_path.parent.mkdir()
  pd.DataFrame({"record_id": ["pubmed:1"], "score": [0.9]}).to_parquet(ranking_path)
  common.append_log("screening", {
    "event": "decision", "stage": screening.STAGE, "screener": "KM", "decision_id": "old",
    "record_id": "pubmed:1", "label": "include",
  })
  common.append_log("screening", {
    "event": "rank", "iteration": 1, "ranking_file": common.relative(ranking_path), "training": [],
  })
  common.append_log("chase", {"event": "round", "seeds": ["pubmed:1"]})
  common.append_log("extraction", {"event": "export", "included": 1})
  common.append_log("search", {"event": "search", "run": "old"})
  return {
    common.relative(path): path.read_bytes()
    for path in tmp_path.rglob("*") if path.is_file()
  }


def fresh_search(cfg):
  records = common.records_frame([
    {"database": "pubmed", "source_id": str(i), "pmid": str(i), "doi": f"10.1/{i}",
     "title": f"Study {i}", "year": 2020, "language": "en"}
    for i in range(1, 6)
  ])
  run_dir = common.RAW_DIR / "new"
  run_dir.mkdir()
  records.to_csv(run_dir / "records.csv", index=False)
  (run_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
  common.append_log("search", {"event": "search", "run": "new"})
  return records


def test_fresh_rebuild_archives_history_and_restarts_the_seed_order(previous_run, monkeypatch, settings):
  monkeypatch.setattr("litmap.ui.rebuild.search.run_search", fresh_search)
  job = SearchRebuild()
  job.start({"filters": {"languages": ["en"]}})
  job.thread.join(5)

  result = job.state()
  assert result["status"] == "complete"
  archive = common.ROOT / result["archive"]
  for relative, contents in previous_run.items():
    assert (archive / relative).read_bytes() == contents

  workspace = screening.Workspace.load(settings=settings, search_cfg={"validation": []})
  assert workspace.decisions == {}
  assert workspace.ranking is None
  assert workspace.progress()["screened"] == 0
  assert workspace.order == screening.queue_order(workspace.pool.index, set(), settings.seed, settings.seed_set_size)
  assert workspace.next_record_id() == workspace.order[0]
  assert len(workspace.pool) == 5
  assert common.chase_rounds() == []
  assert common.read_log("chase") == []
  assert common.read_log("extraction") == []
  assert list(common.MODELS_DIR.iterdir()) == []
  assert list(common.PROCESSED_DIR.iterdir()) == []
  assert list(common.OUTPUTS_DIR.iterdir()) == []


@pytest.mark.parametrize("stage", ["search.run_search", "dedup.deduplicate", "rules.apply_rules"])
def test_failed_fresh_rebuild_restores_the_previous_run(previous_run, monkeypatch, stage):
  monkeypatch.setattr("litmap.ui.rebuild.search.run_search", fresh_search)

  def fail(*args, **kwargs):
    raise RuntimeError("Stage failed")

  monkeypatch.setattr(f"litmap.ui.rebuild.{stage}", fail)
  job = SearchRebuild()
  job.start({"filters": {"languages": ["en"]}})
  job.thread.join(5)

  result = job.state()
  assert result["status"] == "failed"
  assert result["restored"] is True
  for relative, contents in previous_run.items():
    assert (common.ROOT / relative).read_bytes() == contents
  assert screening.latest_ranking(common.read_log("screening")).iteration == 1
  assert screening.replay_decisions(common.read_log("screening"), "KM")["pubmed:1"]["label"] == "include"
  assert (common.ROOT / result["archive"] / "failed").is_dir()
