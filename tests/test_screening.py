import dataclasses

import pandas as pd
import pytest

from litmap import common, screening

SEARCH_CONFIG = {"validation": [{"label": "Key study", "doi": "10.1/4"}]}


def _decision(decision_id, record_id, label, screener="KM"):
  return {
    "event": "decision",
    "stage": screening.STAGE,
    "decision_id": decision_id,
    "record_id": record_id,
    "label": label,
    "screener": screener,
  }


def _pool(count=5):
  record_ids = [f"pubmed:{number}" for number in range(count)]

  return pd.DataFrame({
    "title": [f"Title {number}" for number in range(count)],
    "doi": [f"10.1/{number}" for number in range(count)],
    "pmid": [str(number) for number in range(count)],
  }, index=pd.Index(record_ids, name="record_id"))


@pytest.fixture
def workspace(tmp_path, monkeypatch, settings):
  monkeypatch.setattr(common, "LOG_DIR", tmp_path)

  return screening.Workspace.load(settings=settings, search_cfg=SEARCH_CONFIG, pool=_pool(), events=[])


def test_undo_reverts_exactly_the_decision_it_names():
  events = [
    _decision("a", "pubmed:1", "include"),
    _decision("b", "pubmed:2", "exclude"),
    {"event": "undo", "stage": screening.STAGE, "undoes": "b", "record_id": "pubmed:2", "screener": "KM"},
    _decision("c", "pubmed:2", "maybe"),
    _decision("d", "pubmed:3", "exclude", screener="XY"),  # another screener's decisions don't count
  ]

  decisions = screening.replay_decisions(events, "KM")

  labels = {record_id: decision["label"] for record_id, decision in decisions.items()}

  assert labels == {"pubmed:1": "include", "pubmed:2": "maybe"}


def test_irrelevant_in_a_row_counts_excludes_since_the_last_relevant_record():
  events = [_decision("a", "r1", "exclude"), _decision("b", "r2", "maybe"), _decision("c", "r3", "exclude")]

  assert screening.irrelevant_in_a_row(screening.replay_decisions(events, "KM")) == 1


def test_queue_order_is_fixed_and_hides_key_studies_inside_the_seed_set():
  record_ids = [f"r{number}" for number in range(50)]

  order = screening.queue_order(record_ids, {"r7", "r8"}, seed=3, seed_set_size=10)

  assert order == screening.queue_order(record_ids, {"r7", "r8"}, seed=3, seed_set_size=10)
  assert sorted(order) == sorted(record_ids)
  assert {"r7", "r8"} <= set(order[:12])  # inside the seed set (it grows by the inserted key studies)


def test_decisions_and_undos_are_logged_and_replayed(workspace, settings):
  first = workspace.next_record_id()
  workspace.decide(first, "exclude", reason="not_running", seconds=4)
  workspace.undo_last()

  events = common.read_log("screening")

  replayed = screening.Workspace.load(
    settings=settings, search_cfg=SEARCH_CONFIG, pool=_pool(), events=events,
  )

  assert replayed.next_record_id() == first
  assert [event["event"] for event in common.read_log("screening")] == ["decision", "undo"]


def test_progress_counts_found_key_studies(workspace):
  while workspace.next_record_id():
    workspace.decide(workspace.next_record_id(), "include")

  progress = workspace.progress()

  assert progress["screened"] == 5
  assert (progress["key_studies_found"], progress["key_studies_total"]) == (1, 1)


@pytest.mark.parametrize("label, reason, note, message", [
  ("skip", None, None, "Unknown decision"),
  ("exclude", None, None, "Choose a reason"),
  ("exclude", "other", "  ", "Add a note"),
])
def test_invalid_decisions_are_refused(workspace, label, reason, note, message):
  with pytest.raises(screening.DecisionError, match=message):
    workspace.decide(workspace.next_record_id(), label, reason=reason, note=note)


def test_a_decision_for_a_record_not_due_next_is_refused(workspace):
  not_next = next(record_id for record_id in workspace.order if record_id != workspace.next_record_id())

  with pytest.raises(screening.StaleRecordError):
    workspace.decide(not_next, "include")


def _ranking(order, trained_on=0):
  scores = {record_id: 1 - position / 10 for position, record_id in enumerate(order)}

  return screening.Ranking(iteration=1, trained_on=trained_on, order=order, scores=scores)


def test_the_seed_set_keeps_its_random_order_and_the_ranking_orders_the_rest():
  ranking = _ranking(["r9", "r5", "r1"])

  order = screening.screening_order(["r1", "r2", "r5", "r9", "r7"], seed_set_size=2, ranking=ranking)

  assert order == ["r1", "r2", "r9", "r5", "r7"]  # r7 isn't ranked, so it follows in random order


def test_the_ranker_is_due_after_the_seed_set_and_then_after_every_batch(settings):
  settings = dataclasses.replace(settings, seed_set_size=2, ranker={"batch_size": 2})

  workspace = screening.Workspace(
    settings=settings, pool=_pool(), order=list(_pool().index), key_ids=set(), key_total=0, decisions={},
  )

  def decide(*record_ids):
    workspace.decisions.update({record_id: {"label": "exclude"} for record_id in record_ids})

  decide("pubmed:0")
  assert not workspace.ranker_due()  # seed set not done

  decide("pubmed:1")
  assert workspace.ranker_due()  # seed set done, no ranking yet

  workspace.ranking = _ranking(["pubmed:2", "pubmed:3", "pubmed:4"], trained_on=2)
  decide("pubmed:2")
  assert not workspace.ranker_due()  # 1 of 2 new decisions

  decide("pubmed:3")
  assert workspace.ranker_due()


def test_ranked_decisions_log_the_iteration_rank_and_score(tmp_path, monkeypatch, settings):
  monkeypatch.setattr(common, "LOG_DIR", tmp_path)
  settings = dataclasses.replace(settings, seed_set_size=1)

  workspace = screening.Workspace(
    settings=settings, pool=_pool(), order=list(_pool().index), key_ids=set(), key_total=0,
    decisions={"pubmed:0": {"label": "include"}},
    ranking=_ranking(["pubmed:3", "pubmed:1"]),
  )

  event = workspace.decide("pubmed:1", "maybe")

  assert (event["queue"], event["ranker_iteration"], event["rank"]) == ("ranker", 1, 2)
  assert event["score"] == 0.9


def test_a_second_screener_screens_a_fixed_blind_sample(tmp_path, monkeypatch, settings):
  monkeypatch.setattr(common, "LOG_DIR", tmp_path)
  settings = dataclasses.replace(settings, second_sample_size=3)
  events = [_decision("a", "pubmed:0", "include")]  # KM's decision: the second screener never sees it

  def load():
    return screening.Workspace.load(
      settings=settings, search_cfg=SEARCH_CONFIG, pool=_pool(), events=events, screener="XY",
    )

  second = load()
  event = second.decide(second.next_record_id(), "include")

  assert second.blind and len(second.order) == 3
  assert second.order == load().order
  assert second.decisions == {event["record_id"]: event}
  assert (event["screener"], event["queue"]) == ("XY", screening.SECOND_SCREENER_QUEUE)


def test_the_feed_numbers_decisions_and_undos_newest_first(workspace):
  first = workspace.next_record_id()
  workspace.decide(first, "include")
  workspace.undo_last()

  feed = workspace.feed()

  assert [(item["number"], item["event"]) for item in feed] == [(2, "undo"), (1, "decision")]
