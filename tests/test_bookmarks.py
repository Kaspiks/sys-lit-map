import pandas as pd
import pytest

from litmap import bookmarks, common
from litmap.ui.rebuild import RunArchive

RECORDS = pd.DataFrame({
  "title": ["Music and running", "Cycling"],
  "year": [2012, 2020],
  "journal": ["J", None],
  "doi": ["10.1/a", None],
}, index=pd.Index(["pubmed:1", "openalex:W2"], name="record_id"))


@pytest.fixture(autouse=True)
def folders(tmp_path, monkeypatch):
  monkeypatch.setattr(common, "ROOT", tmp_path)

  for name, relative in {
    "LOG_DIR": "logs", "CONFIG_DIR": "config", "RAW_DIR": "data/raw", "INTERIM_DIR": "data/interim",
    "PROCESSED_DIR": "data/processed", "MODELS_DIR": "models", "OUTPUTS_DIR": "outputs",
  }.items():
    monkeypatch.setattr(common, name, tmp_path / relative)


def test_bookmarks_replay_per_screener_and_a_new_note_replaces_the_old():
  bookmarks.add("pubmed:1", "KM", note="unusual tempo", records=RECORDS)
  bookmarks.add("openalex:W2", "KM", records=RECORDS)
  bookmarks.add("pubmed:1", "KM", note="unusual tempo, check the methods", records=RECORDS)
  bookmarks.add("pubmed:1", "XY", records=RECORDS)
  bookmarks.remove("openalex:W2", "KM")

  current = bookmarks.bookmarks("KM")

  assert list(current) == ["pubmed:1"]
  assert current["pubmed:1"]["note"] == "unusual tempo, check the methods"
  assert (current["pubmed:1"]["title"], current["pubmed:1"]["doi"]) == ("Music and running", "10.1/a")
  assert list(bookmarks.bookmarks("XY")) == ["pubmed:1"]


def test_only_known_records_can_be_bookmarked():
  with pytest.raises(KeyError):
    bookmarks.add("pubmed:404", "KM", records=RECORDS)


def test_a_new_search_archives_the_run_but_keeps_the_bookmarks():
  bookmarks.add("pubmed:1", "KM", records=RECORDS)
  common.append_log("screening", {"event": "decision", "record_id": "pubmed:1"})

  archive = RunArchive()
  archive.capture()

  assert list(bookmarks.bookmarks("KM")) == ["pubmed:1"]
  assert common.read_log("screening") == []
  assert (archive.path / "logs" / "bookmarks.jsonl").exists()
