import dataclasses

import pandas as pd
import pytest
from scipy import sparse

from litmap import common, rank, screening


def _workspace(settings, labels):
  record_ids = ["openalex:W1", "pubmed:2", "pubmed:3", "pubmed:4"]

  pool = pd.DataFrame({
    "title": ["A", "B", "C", "D"],
    "doi": [None] * 4,
    "pmid": [None] * 4,
  }, index=pd.Index(record_ids, name="record_id"))

  events = [
    {"event": "decision", "stage": screening.STAGE, "decision_id": str(number),
     "record_id": record_id, "label": label, "screener": settings.screener}
    for number, (record_id, label) in enumerate(labels.items())
  ]

  return screening.Workspace.load(settings=settings, search_cfg={}, pool=pool, events=events)


def test_include_and_maybe_count_as_relevant(settings):
  workspace = _workspace(settings, {"pubmed:2": "include", "pubmed:3": "maybe", "pubmed:4": "exclude"})

  labelled, unlabelled = rank.split_by_label(workspace)

  assert labelled["label"].to_dict() == {"pubmed:2": 1, "pubmed:3": 1, "pubmed:4": 0}
  assert list(unlabelled.index) == ["openalex:W1"]
  assert "label" not in unlabelled.columns


def test_training_waits_for_enough_labels_of_both_classes(settings):
  labelled, _ = rank.split_by_label(_workspace(settings, {"pubmed:2": "include", "pubmed:3": "exclude"}))

  assert rank.enough_labels(labelled, {"min_labels": {"relevant": 1, "irrelevant": 1}})
  assert not rank.enough_labels(labelled, {"min_labels": {"relevant": 2, "irrelevant": 1}})


def test_ranking_puts_high_scores_first_and_breaks_ties_by_record_id(settings):
  _, unlabelled = rank.split_by_label(_workspace(settings, {}))

  ranking = rank.rank(unlabelled, [0.2, 0.9, 0.2, 0.5])

  assert list(ranking["record_id"]) == ["pubmed:2", "pubmed:4", "openalex:W1", "pubmed:3"]


CONCEPTS = {
  "population": ["runner*"],
  "exposure": ["metronome*", "music*"],
  "outcome": ["cadence", "synchroni*"],
}


def _records():
  return pd.DataFrame({
    "title": ["Runners and metronomes", "Cycling to music", "Editorial"],
    "abstract": ["Cadence of runners synchronised to a metronome.", None, "Opinion."],
    "year": pd.array([2015, None, 2020], dtype="Int64"),
    "pub_types": ["Journal Article", "conference-abstract", "Editorial; Retracted Publication"],
    "databases": ["europepmc+openalex+pubmed", "openalex", "pubmed"],
  }, index=pd.Index(["pubmed:1", "openalex:W2", "pubmed:3"], name="record_id"))


@pytest.mark.parametrize("pub_types, expected", [
  ("Journal Article; Review", "review"),
  ("conference-abstract", "conference"),
  ("book-review", "opinion"),
  ("book-chapter", "book"),
  ("Preprint", "preprint"),
  ("article", "article"),
  ("dataset", "other"),
  ("", "other"),
])
def test_record_types_from_every_database_map_to_one_vocabulary(pub_types, expected):
  assert rank.record_type(pub_types) == expected


def test_feature_matrices_share_columns_and_keep_row_order():
  records = _records()
  labelled = records.iloc[:2].assign(label=[1, 0])

  features = rank.build_features(labelled, records.iloc[2:], {"features": ["concepts", "metadata"]}, CONCEPTS)
  first = dict(zip(features.names, features.labelled.toarray()[0]))
  editorial = dict(zip(features.names, features.unlabelled.toarray()[0]))

  assert features.labelled.shape == (2, len(features.names))
  assert features.unlabelled.shape == (1, len(features.names))
  assert first["concept:blocks_found"] == 1.0  # all three blocks, the most of any record
  assert first["meta:in_pubmed"] == 1.0
  assert editorial["meta:retracted"] == 1.0
  assert editorial["meta:type_opinion"] == 1.0


def test_unknown_feature_groups_are_refused():
  records = _records()

  with pytest.raises(ValueError, match="embeddings"):
    rank.build_features(
      records.iloc[:1].assign(label=[1]), records.iloc[1:], {"features": ["embeddings"]}, CONCEPTS,
    )


@pytest.mark.parametrize("model", ["random_forest", "logistic_regression"])
def test_both_models_score_records_like_the_relevant_ones_higher(model):
  features = sparse.csr_matrix([[1, 0], [1, 0], [0, 1], [0, 1]])
  labels = pd.Series([1, 1, 0, 0], dtype="Int64")

  trained = rank.train(features, labels, {"model": model, "n_estimators": 10}, seed=1)
  scores = rank.score(trained, sparse.csr_matrix([[1, 0], [0, 1]]))

  assert scores[0] > scores[1]


def test_unknown_models_are_refused():
  with pytest.raises(ValueError, match="neural_net"):
    rank.train(sparse.csr_matrix([[1], [0]]), pd.Series([1, 0]), {"model": "neural_net"}, seed=1)


def test_a_logged_iteration_replays_exactly(tmp_path, monkeypatch, settings):
  for name, path in {"ROOT": tmp_path, "INTERIM_DIR": tmp_path / "interim",
                     "LOG_DIR": tmp_path / "logs", "MODELS_DIR": tmp_path / "models"}.items():
    monkeypatch.setattr(common, name, path)

  pool = pd.concat([_records()] * 3, ignore_index=True)
  pool.index = pd.Index([f"pubmed:{number}" for number in range(len(pool))], name="record_id")
  pool = pool.assign(doi=None, pmid=None, excluded_by=None).reset_index()
  common.INTERIM_DIR.mkdir()
  pool.to_parquet(common.INTERIM_DIR / "prefiltered.parquet")

  for number, label in enumerate(["include", "exclude", "include", "exclude"]):
    common.append_log("screening", {
      "event": "decision", "stage": screening.STAGE, "decision_id": str(number),
      "record_id": f"pubmed:{number}", "label": label, "screener": settings.screener,
    })

  ranker_settings = {
    "min_labels": {"relevant": 1, "irrelevant": 1},
    "n_estimators": 20,
    "tfidf": {"min_df": 1},
  }
  settings = dataclasses.replace(settings, ranker=ranker_settings)

  assert rank.main(settings=settings, concepts=CONCEPTS, verbose=False) is not None
  assert rank.replay(1)
