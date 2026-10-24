import pandas as pd
import pytest

from litmap import common, screening
from litmap.ui.app import create_app
from litmap.ui.views import abstract_sections

CONCEPTS = {"population": ["runner*"], "exposure": ['"auditory cue"', "metronome*"], "outcome": []}


def test_structured_abstracts_are_split_at_their_labels():
  sections = abstract_sections("BACKGROUND: Why. METHODS: How, p<0.05.")

  assert sections == [("Background", "Why."), ("Methods", "How, p<0.05.")]


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
  monkeypatch.setattr(common, "LOG_DIR", tmp_path)

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

  def load_workspace():
    events = common.read_log("screening")

    return screening.Workspace.load(settings=settings, search_cfg={}, pool=pool, events=events)

  app = create_app(
    load_workspace=load_workspace,
    load_search_config=lambda: {"concepts": CONCEPTS},
    retrain=ranker,
  )

  return app.test_client()


def test_screening_flow_decide_and_undo(client):
  first = client.get("/api/state").get_json()["record"]["record_id"]

  decision = {"record_id": first, "label": "maybe", "seconds": 3}

  after_decision = client.post("/api/decisions", json=decision).get_json()
  after_undo = client.post("/api/undo", json={}).get_json()

  assert after_decision["progress"]["screened"] == 1
  assert after_decision["recent"][0]["label"] == "maybe"
  assert after_undo["record"]["record_id"] == first
  assert after_undo["progress"]["screened"] == 0


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
