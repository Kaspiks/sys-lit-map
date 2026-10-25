import pandas as pd
import pytest
from threading import Event
from concurrent.futures import ThreadPoolExecutor

from litmap import common, screening
from litmap.config import ExclusionReason
from litmap.ui.app import PAGES, create_app
from litmap.ui.views import abstract_sections, concept_check

CONCEPTS = {"population": ["runner*"], "exposure": ['"auditory cue"', "metronome*"], "outcome": []}


def test_structured_abstracts_are_split_at_their_labels():
  sections = abstract_sections("BACKGROUND: Why. METHODS: How, p<0.05.")

  assert sections == [("Background", "Why."), ("Methods", "How, p<0.05.")]


def test_a_block_without_terms_suggests_its_exclusion_reason_and_key():
  reasons = (
    ExclusionReason("other", "Other"),
    ExclusionReason("not_running", "Not running", when_missing="population"),
  )

  check = concept_check({"population": {}, "exposure": {"metronome": 2}}, reasons)

  assert check == [
    {"block": "population", "terms": {}, "suggested_reason": {"key": 2, "label": "Not running"}},
    {"block": "exposure", "terms": {"metronome": 2}, "suggested_reason": None},
  ]


class FakeRanker:
  """Stands in for rank.retrain_if_due: counts calls and can fail on demand."""

  def __init__(self):
    self.calls = 0
    self.error = None

  def __call__(self):
    self.calls += 1

    if self.error:
      raise self.error

    return False


@pytest.fixture
def ranker():
  return FakeRanker()


@pytest.fixture
def client(tmp_path, monkeypatch, settings, ranker):
  monkeypatch.setattr(common, "ROOT", tmp_path)

  for name, relative in {
    "LOG_DIR": "logs", "CONFIG_DIR": "config", "RAW_DIR": "data/raw",
    "INTERIM_DIR": "data/interim", "PROCESSED_DIR": "data/processed",
    "MODELS_DIR": "models", "OUTPUTS_DIR": "outputs",
  }.items():
    monkeypatch.setattr(common, name, tmp_path / relative)

  pool = pd.DataFrame({
    "title": ["Runners and metronomes", "Cycling"],
    "abstract": ["METHODS: Runners ran.", None],
    "journal": ["J", None],
    "year": [2020, None],
    "doi": ["10.1/a", None],
    "pmid": ["1", None],
    "pub_types": ["Journal Article", "article"],
    "databases": ["pubmed", "openalex"],
    "authors": ["Lee A", None],
  }, index=pd.Index(["pubmed:1", "openalex:W2"], name="record_id"))

  def load_workspace(screener=None):
    events = common.read_log("screening")

    return screening.Workspace.load(
      settings=settings, search_cfg={}, pool=pool, events=events, screener=screener,
    )

  app = create_app(
    load_workspace=load_workspace,
    load_search_config=lambda: {"concepts": CONCEPTS},
    load_settings=lambda: settings,
    retrain=ranker,
  )

  return app.test_client()


def test_screening_flow_decide_and_undo(client):
  first = client.get("/api/state").get_json()["record"]["record_id"]

  decision = {"record_id": first, "label": "maybe", "seconds": 3}

  after_decision = client.post("/api/decisions", json=decision).get_json()
  after_undo = client.post("/api/undo", json={}).get_json()

  assert after_decision["progress"]["screened"] == 1
  assert after_decision["feed"][0]["label"] == "maybe"
  assert [item["event"] for item in after_undo["feed"]] == ["undo", "decision"]
  assert after_undo["feed"][0]["label"] == "maybe"  # the label the undo took back
  assert after_undo["record"]["record_id"] == first
  assert after_undo["progress"]["screened"] == 0


def test_reload_config_returns_updated_files(client, tmp_path, monkeypatch):
  monkeypatch.setattr(common, "ROOT", tmp_path)
  config_file = tmp_path / "search.yaml"
  config_file.write_text("question: Original\n")
  monkeypatch.setattr("litmap.ui.app.CONFIG_FILES", (config_file,))

  before = client.get("/api/config").get_json()
  config_file.write_text("question: Updated\n")
  response = client.post("/api/config/reload", json={})

  assert response.status_code == 200
  after = response.get_json()
  assert after["files"][0]["text"] == "question: Updated\n"
  assert after["files"][0]["sha256"] != before["files"][0]["sha256"]
  assert "search_sync" in after


def test_rebuild_runs_all_stages_and_prevents_duplicate_jobs(client, monkeypatch):
  started, release = Event(), Event()
  calls = []

  def run_search(cfg):
    calls.append(("search", cfg))
    started.set()
    assert release.wait(5)

    return [1, 2, 3]

  def deduplicate():
    calls.append(("dedup", None))

    return [1, 2]

  def apply_rules(cfg):
    calls.append(("rules", cfg))

    return pd.DataFrame({"excluded_by": [None, "language_not_allowed"]})

  monkeypatch.setattr("litmap.ui.rebuild.search.run_search", run_search)
  monkeypatch.setattr("litmap.ui.rebuild.dedup.deduplicate", deduplicate)
  monkeypatch.setattr("litmap.ui.rebuild.rules.apply_rules", apply_rules)

  assert client.get("/api/config/rebuild").get_json()["status"] == "idle"
  job = client.application.extensions["search_rebuild"]

  try:
    response = client.post("/api/config/rebuild", json={})
    assert response.status_code == 202
    assert started.wait(5)
    assert client.get("/api/config/rebuild").get_json()["stage"] == "Searching databases"
    blocked = client.get("/api/state")
    assert blocked.status_code == 503
    assert blocked.get_json()["code"] == "search_rebuilding"
    assert blocked.headers["Retry-After"] == "1"
    assert client.post("/api/decisions", json={}).status_code == 503
    assert client.post("/api/undo", json={}).status_code == 503
    thread = job.thread
    client.post("/api/config/rebuild", json={})
    assert job.thread is thread
  finally:
    release.set()
    job.thread.join(5)

  assert not job.thread.is_alive()
  assert [stage for stage, _ in calls] == ["search", "dedup", "rules"]
  assert calls[0][1] == calls[2][1] == {"concepts": CONCEPTS}
  result = client.get("/api/config/rebuild").get_json()
  assert result.pop("archive").startswith("data/archive/")

  assert result == {
    "status": "complete", "stage": "Finished", "found": 3, "records": 2, "to_screen": 1,
  }


def test_rebuild_reports_failure_and_can_be_retried(client, monkeypatch):
  calls = []

  def fail(cfg):
    calls.append(cfg)
    raise RuntimeError("Database unavailable")

  def unexpected_dedup():
    pytest.fail("Deduplication must not run after search fails")

  monkeypatch.setattr("litmap.ui.rebuild.search.run_search", fail)
  monkeypatch.setattr("litmap.ui.rebuild.dedup.deduplicate", unexpected_dedup)
  job = client.application.extensions["search_rebuild"]

  for _ in range(2):
    assert client.post("/api/config/rebuild", json={}).status_code == 202
    job.thread.join(5)
    result = client.get("/api/config/rebuild").get_json()
    assert result.pop("archive").startswith("data/archive/")

    assert result == {
      "status": "failed", "stage": "Searching databases", "error": "Database unavailable", "restored": True,
    }

  assert len(calls) == 2


def test_parallel_reads_wait_for_each_other_without_claiming_a_rebuild(client):
  job = client.application.extensions["search_rebuild"]
  attempted = Event()
  lock = job.access_lock

  class ObservedLock:
    def acquire(self, **kwargs):
      attempted.set()

      return lock.acquire(**kwargs)

    def release(self):
      lock.release()

  job.access_lock = ObservedLock()

  def read_state():
    with client.application.test_client() as other:
      return other.get("/api/state")

  lock.acquire()

  with ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(read_state)

    try:
      assert attempted.wait(5)
    finally:
      lock.release()

    response = future.result(timeout=5)

  assert response.status_code == 200
  assert "record" in response.get_json()


def test_stale_decisions_get_409_and_the_current_state(client):
  first = client.get("/api/state").get_json()["record"]["record_id"]
  client.post("/api/decisions", json={"record_id": first, "label": "include"})

  response = client.post("/api/decisions", json={"record_id": first, "label": "include"})

  assert response.status_code == 409
  assert response.get_json()["state"]["record"]["record_id"] != first


def test_invalid_decisions_get_400(client):
  first = client.get("/api/state").get_json()["record"]["record_id"]

  response = client.post("/api/decisions", json={"record_id": first, "label": "exclude"})

  assert response.status_code == 400
  assert "reason" in response.get_json()["error"]


def test_each_decision_gives_the_ranker_a_chance_to_retrain(client, ranker):
  first = client.get("/api/state").get_json()["record"]["record_id"]

  client.post("/api/decisions", json={"record_id": first, "label": "include"})

  assert ranker.calls == 1


def test_a_failing_ranker_never_loses_the_decision(client, ranker):
  ranker.error = RuntimeError("model crashed")
  first = client.get("/api/state").get_json()["record"]["record_id"]

  response = client.post("/api/decisions", json={"record_id": first, "label": "include"})

  assert response.status_code == 200
  assert response.get_json()["progress"]["screened"] == 1


def test_every_page_is_served(client):
  assert all(client.get(path).status_code == 200 for path in PAGES)


def test_a_second_screener_screens_blind_under_their_own_initials(client):
  state = client.get("/api/state?screener=xy").get_json()

  decision = {"record_id": state["record"]["record_id"], "label": "include", "screener": "XY"}
  client.post("/api/decisions", json=decision)

  logged = common.read_log("screening")[-1]

  assert state["progress"]["blind"] and state["queue"] == screening.SECOND_SCREENER_QUEUE
  assert logged["screener"] == "XY"
  assert client.get("/api/state").get_json()["progress"]["screened"] == 0  # KM's view is untouched


def test_screener_initials_are_checked(client):
  assert client.get("/api/state?screener=x1").status_code == 400


def test_only_known_logs_can_be_read(client):
  assert client.get("/api/events?log=../config").status_code == 400
  assert client.get("/api/events?log=screening").get_json()["total"] == 0


def _write_records(*record_ids):
  """The deduplicated, rule-checked records the registry and bookmarks read."""
  common.INTERIM_DIR.mkdir(parents=True, exist_ok=True)

  pd.DataFrame({
    "record_id": list(record_ids),
    "title": [f"Title of {record_id}" for record_id in record_ids],
    "year": [2020] * len(record_ids),
    "databases": ["pubmed"] * len(record_ids),
    "excluded_by": [None] * len(record_ids),
  }).to_parquet(common.INTERIM_DIR / "prefiltered.parquet", index=False)


def test_a_bookmark_keeps_the_record_on_screen_and_decides_nothing(client):
  _write_records("pubmed:1", "openalex:W2")
  first = client.get("/api/state").get_json()["record"]["record_id"]

  added = client.post("/api/bookmarks", json={"record_id": first, "bookmarked": True}).get_json()
  state = client.get("/api/state").get_json()
  listed = client.get("/api/bookmarks").get_json()

  assert added["bookmarked"] and state["record"]["record_id"] == first and state["record"]["bookmarked"]
  assert state["progress"]["screened"] == 0
  assert [(mark["record_id"], mark["status"]) for mark in listed] == [(first, "to_screen")]

  client.post("/api/bookmarks", json={"record_id": first, "bookmarked": False})

  assert not client.get("/api/state").get_json()["record"]["bookmarked"]
  assert client.get("/api/bookmarks").get_json() == []


def test_unknown_records_cant_be_bookmarked(client):
  _write_records("pubmed:1")

  assert client.post("/api/bookmarks", json={"record_id": "pubmed:404"}).status_code == 404
