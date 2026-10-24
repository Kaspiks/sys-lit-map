import json

import pandas as pd
import pytest

from litmap import chase, common, dedup, screening

RECORD_IDS = ["pubmed:1", "openalex:W2", "pubmed:3"]


def _pool():
  return pd.DataFrame({
    "doi": ["10.1/one", None, "10.1/three"],
    "pmid": ["1", None, "3"],
    "member_ids": ["pubmed:1; openalex:W100", "openalex:W2", "pubmed:3"],
  }, index=pd.Index(RECORD_IDS, name="record_id"))


def _work(work_id, doi=None):
  return {"id": f"https://openalex.org/{work_id}", "doi": f"https://doi.org/{doi}" if doi else None,
          "title": f"Work {work_id}", "publication_year": 2020}


@pytest.fixture
def folders(tmp_path, monkeypatch):
  for name, path in {"ROOT": tmp_path, "RAW_DIR": tmp_path / "raw", "INTERIM_DIR": tmp_path / "interim",
                     "LOG_DIR": tmp_path / "logs"}.items():
    monkeypatch.setattr(common, name, path)

  return tmp_path


def test_seeds_are_included_records_no_earlier_round_chased(settings):
  workspace = screening.Workspace(
    settings=settings, pool=_pool(), order=RECORD_IDS, key_ids=set(), key_total=0,
    decisions={
      "pubmed:1": {"label": "include"},
      "openalex:W2": {"label": "include"},
      "pubmed:3": {"label": "maybe"},
    },
  )

  seeds = chase.seed_records(workspace, ["include"], [{"event": "round", "seeds": ["pubmed:1"]}])

  assert list(seeds.index) == ["openalex:W2"]


def test_openalex_ids_come_from_member_ids_and_lookups(monkeypatch):
  looked_up = []

  def fake_lookup(doi, pmid):
    looked_up.append((doi, pmid))

    return None  # OpenAlex doesn't know it

  monkeypatch.setattr(chase, "lookup_openalex_id", fake_lookup)

  ids = chase.openalex_ids(_pool())

  assert ids == {"pubmed:1": "W100", "openalex:W2": "W2"}
  assert looked_up == [("10.1/three", "3")]


def test_unseen_skips_known_works_and_keeps_each_new_one_once():
  found = {
    "backward": [_work("W2"), _work("W7"), _work("W8", doi="10.1/known")],
    "forward": [_work("W7"), _work("W9")],
  }

  works, found_by = chase.unseen(found, known_ids={"W2"}, known_dois={"10.1/known"})

  assert [work["id"].rsplit("/", 1)[-1] for work in works] == ["W7", "W9"]
  assert found_by == {"W7": ["backward", "forward"], "W9": ["forward"]}


def test_a_saved_round_is_logged_and_merged_by_dedup(folders):
  search_run = common.RAW_DIR / "20260101T000000Z"
  search_run.mkdir(parents=True)

  common.records_frame([
    {"database": "pubmed", "source_id": "1", "pmid": "1", "doi": "10.1/one", "title": "Running to music"},
  ]).to_csv(search_run / "records.csv", index=False)

  dedup.deduplicate(search_run)

  works = [_work("W7"), _work("W8", doi="10.1/one")]  # W8 is the same paper the search found

  round_dir = chase.save_round(["pubmed:1"], {"pubmed:1": "W100"}, {"backward": works}, works,
                               {"W7": ["backward"], "W8": ["backward"]})

  merged = dedup.deduplicate(search_run)

  logged = json.loads((common.LOG_DIR / "chase.jsonl").read_text())
  assert logged["seeds"] == ["pubmed:1"] and logged["new_works"]["W7"] == ["backward"]
  assert (round_dir / "openalex.json").exists()
  assert len(merged) == 2  # W8 merged into the search record by DOI, W7 is new
  assert "pubmed:1" in set(merged["record_id"])  # the existing record id is kept


def test_long_id_lists_are_fetched_in_batches_of_at_most_100(monkeypatch):
  filters = []

  def fake_fetch(query_params):
    filters.append(query_params["filter"])

    return 1, [_work(f"W{len(filters)}")]

  monkeypatch.setattr(chase.openalex, "fetch", fake_fetch)

  works = chase.fetch_works("cites", [f"W{number}" for number in range(250)])

  assert [len(f.removeprefix("cites:").split("|")) for f in filters] == [100, 100, 50]
  assert len(works) == 3


def test_references_fetches_each_cited_work_once(monkeypatch):
  seeds = [_work("W1") | {"referenced_works": ["https://openalex.org/W7", "https://openalex.org/W8"]},
           _work("W2") | {"referenced_works": ["https://openalex.org/W8"]}]

  requested = []

  def fake_fetch_works(filter_name, ids):
    requested.append(list(ids))

    return seeds if len(requested) == 1 else [_work(work_id) for work_id in ids]

  monkeypatch.setattr(chase, "fetch_works", fake_fetch_works)

  cited = chase.references(["W1", "W2"])

  assert requested == [["W1", "W2"], ["W7", "W8"]]  # W8, cited by both seeds, is fetched once
  assert [chase.work_id(work) for work in cited] == ["W7", "W8"]
