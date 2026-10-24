"""Stage 3: screening, step 2: ranker (recall-oriented screening prioritisation).

One iteration trains on every decision logged so far and ranks the records not screened yet,
most likely relevant first. Planned (docs/architecture.md): a random forest on TF-IDF of title
and abstract plus metadata features, compared against logistic regression, retrained after
each batch of decisions.

The plumbing is done: loading the labels from the log, refusing to train on too few, a
reproducible ranking order, and saving and logging each iteration for replay.py.
The functions marked TODO are yours: train and score.

Run one iteration:  python -m litmap.rank
Step through it:    debug -m litmap.rank, then in VS Code: Run and Debug -> "Attach to container"
Scores are for you and the evaluation only; the screening screen never shows them.
"""

import re
from typing import NamedTuple

import joblib
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.ensemble import RandomForestClassifier

from litmap import common, dedup, screening
from litmap.concepts import ConceptTerms
from litmap.config import load_config, load_screening_settings

RELEVANT = 1  # include or maybe: a "maybe" still has to be read in full, so the ranker should find them
IRRELEVANT = 0
DEFAULT_MIN_LABELS = {"relevant": 5, "irrelevant": 5}
TOP_TO_PRINT = 10

# record types from every database's vocabulary; the first pattern that matches wins
RECORD_TYPES = {
  "opinion": r"editorial|comment|letter|book-review",
  "review": r"review|meta-analysis",
  "preprint": r"preprint",
  "conference": r"conference|congress",
  "thesis": r"dissertation|thesis",
  "book": r"book|reference-entry",
  "article": r"journal article|research-article|\barticle\b",
}

OTHER_RECORD_TYPE = "other"


class FeatureBlock(NamedTuple):
  matrix: object  # sparse matrix, one row per record
  names: list


class Features(NamedTuple):
  labelled: object  # sparse matrix, one row per labelled record
  unlabelled: object  # sparse matrix, one row per record not screened yet
  names: list  # one name per column, e.g. "word:cadence", "concept:outcome_in_title"


def main():
  """One ranker iteration. Returns the ranking, or None while there are too few labels."""
  settings = load_screening_settings()
  workspace = screening.Workspace.load(settings=settings)
  labelled, unlabelled = split_by_label(workspace)

  if not enough_labels(labelled, settings.ranker):
    return None

  features = build_features(labelled, unlabelled, settings.ranker, load_config()["concepts"])
  model = train(features.labelled, labelled["label"], settings.ranker, seed=settings.seed)
  ranking = rank(unlabelled, score(model, features.unlabelled))
  save_iteration(model, ranking, labelled, settings)
  print_top(ranking)

  return ranking


def split_by_label(workspace):
  """The screening pool as (labelled records with a `label` column, records not screened yet).

  Both keep every column of the pool: title, abstract, journal, year, pub_types, databases,
  n_records, the rule_* flags and more. The index is the record id.
  """

  labels = {
    record_id: RELEVANT if decision["label"] in screening.RELEVANT_LABELS else IRRELEVANT
    for record_id, decision in workspace.decisions.items()
  }

  pool = workspace.pool.assign(label=pd.Series(labels, dtype="Int64"))
  is_labelled = pool["label"].notna()

  return pool[is_labelled], pool[~is_labelled].drop(columns="label")


def enough_labels(labelled, ranker_settings):
  """True once both classes have the configured minimum; otherwise says what is missing."""
  minimum = ranker_settings.get("min_labels", DEFAULT_MIN_LABELS)

  counts = {
    "relevant": int((labelled["label"] == RELEVANT).sum()),
    "irrelevant": int((labelled["label"] == IRRELEVANT).sum()),
  }

  if all(counts[name] >= minimum[name] for name in counts):
    return True

  print(f"Not enough labels to train yet: {counts}, need at least {minimum}. Keep screening.")

  return False


def build_features(labelled, unlabelled, ranker_settings, concepts):
  """Feature matrices for the labelled records and the records not screened yet.

  The groups come from ranker_settings["features"] (default: all of FEATURE_GROUPS), so
  dropping one in screening.yaml is the plan's ablation. Every group is built from all records
  together: none of them sees the labels, and the two matrices share their columns.
  """

  groups = ranker_settings.get("features", list(FEATURE_GROUPS))
  unknown = set(groups) - set(FEATURE_GROUPS)

  if unknown:
    raise ValueError(f"Unknown feature groups {sorted(unknown)}; choose from {list(FEATURE_GROUPS)}")

  records = pd.concat([labelled.drop(columns="label"), unlabelled])
  blocks = [FEATURE_GROUPS[group](records, ranker_settings, concepts) for group in groups]
  matrix = sparse.hstack([block.matrix for block in blocks]).tocsr()
  names = [name for block in blocks for name in block.names]

  return Features(labelled=matrix[:len(labelled)], unlabelled=matrix[len(labelled):], names=names)


def word_features(records, ranker_settings, concepts):
  """TF-IDF of title + abstract."""
  vectorizer = TfidfVectorizer(**_tfidf_options(ranker_settings))
  matrix = vectorizer.fit_transform(_record_text(records))

  return FeatureBlock(matrix, [f"word:{term}" for term in vectorizer.get_feature_names_out()])


def concept_features(records, ranker_settings, concepts):
  """How often each search.yaml concept block occurs in the title and in the abstract.

  Unlike the rules these never exclude anything: a record without outcome terms, say, only
  scores lower if your decisions show that matters.
  """

  concept_terms = ConceptTerms(concepts)

  counts = {
    part: pd.DataFrame(list(records[part].fillna("").map(concept_terms.hit_counts)), index=records.index)
    for part in ("title", "abstract")
  }

  frame = pd.concat([counts[part].add_suffix(f"_in_{part}") for part in counts], axis=1)
  frame["blocks_found"] = (counts["title"] + counts["abstract"] > 0).sum(axis=1)

  return _numeric_block("concept", np.log1p(frame))  # log: 23 hits isn't 23 times as telling as 1


def metadata_features(records, ranker_settings, concepts):
  """Year, abstract or not, which databases found the record, its type, retracted, trial."""
  pub_types = records["pub_types"].fillna("")
  year = pd.to_numeric(records["year"], errors="coerce").astype(float)
  found_in = records["databases"].str.split("+")
  record_types = pub_types.map(record_type)

  frame = pd.DataFrame({
    "year": year.fillna(year.median()),
    "has_abstract": records["abstract"].notna(),
    "retracted": pub_types.str.contains("retracted", case=False),
    "trial": pub_types.str.contains("trial", case=False),
  }, index=records.index)

  for database in dedup.DATABASE_PRIORITY:
    frame[f"in_{database}"] = [database in databases for databases in found_in]

  for type_name in [*RECORD_TYPES, OTHER_RECORD_TYPE]:
    frame[f"type_{type_name}"] = record_types == type_name

  return _numeric_block("meta", frame.astype(float))


FEATURE_GROUPS = {"words": word_features, "concepts": concept_features, "metadata": metadata_features}


def record_type(pub_types):
  """'Journal Article; Review' -> 'review', 'conference-abstract' -> 'conference', ..."""
  lowered = pub_types.lower()

  matching = (name for name, pattern in RECORD_TYPES.items() if re.search(pattern, lowered))

  return next(matching, OTHER_RECORD_TYPE)


def _numeric_block(prefix, frame):
  """Columns scaled to 0..1, so a linear model weighs them like the TF-IDF values."""
  low, high = frame.min(), frame.max()
  scaled = (frame - low) / (high - low).replace(0, 1)

  names = [f"{prefix}:{column}" for column in frame.columns]

  return FeatureBlock(sparse.csr_matrix(scaled.to_numpy(dtype=float)), names)


def _record_text(records):
  """Title and abstract as one string per record; missing parts become empty."""

  return records["title"].fillna("") + " " + records["abstract"].fillna("")


def _tfidf_options(ranker_settings):
  """The tfidf settings from screening.yaml, as TfidfVectorizer expects them.

  YAML has no tuples, so ngram_range arrives as a list.
  """

  options = dict(ranker_settings.get("tfidf", {}))

  if "ngram_range" in options:
    options["ngram_range"] = tuple(options["ngram_range"])

  return options


def train(features, labels, ranker_settings, seed):
  """TODO: fit a model on the labelled features and return it.

  The plan's model is sklearn.ensemble.RandomForestClassifier, with n_estimators and
  class_weight from ranker_settings, compared against sklearn.linear_model.LogisticRegression.
  Pass random_state=seed: without it the ranking changes on every run and can't be replayed.
  """

  model = RandomForestClassifier(
    n_estimators=ranker_settings.get("n_estimators", 100),
    class_weight=ranker_settings.get("class_weight", "balanced"),
    random_state=seed,
  )

  model.fit(features, labels)

  return model

def score(model, features):
  """TODO: one number per record, higher meaning more likely relevant (e.g. from predict_proba)."""
  scores = model.predict_proba(features)[:, 1]

  return scores

def rank(unlabelled, scores):
  """Records not screened yet, most likely relevant first.

  Ties go by record id, so the same scores always give the same order.
  """

  ranking = unlabelled.assign(score=scores).reset_index()

  return ranking.sort_values(["score", "record_id"], ascending=[False, True], ignore_index=True)


def save_iteration(model, ranking, labelled, settings):
  """Save this iteration's model and ranking, and log them with the training set, for replay.py."""
  iteration = 1 + sum(event.get("event") == "rank" for event in common.read_log("screening"))
  name = f"iteration_{iteration:03d}"
  model_path = common.MODELS_DIR / f"{name}.joblib"
  ranking_path = common.INTERIM_DIR / "rankings" / f"{name}.parquet"

  for path in (model_path, ranking_path):
    path.parent.mkdir(parents=True, exist_ok=True)

  joblib.dump(model, model_path)
  ranking[["record_id", "score"]].to_parquet(ranking_path, index=False)

  common.append_log("screening", {
    "event": "rank",
    "iteration": iteration,
    "timestamp": common.now(),
    "seed": settings.seed,
    "ranker": settings.ranker,
    "training": [[record_id, int(label)] for record_id, label in labelled["label"].items()],
    "model_file": common.relative(model_path),
    "model_sha256": common.sha256(model_path),
    "ranking_file": common.relative(ranking_path),
    "ranking_sha256": common.sha256(ranking_path),
  })

  print(f"Iteration {iteration}: trained on {len(labelled)} decisions, ranked {len(ranking)} records")


def print_top(ranking, count=TOP_TO_PRINT):
  """The highest-scored records, to check the ranker while you develop it."""

  for position, row in ranking.head(count).iterrows():
    print(f"{position + 1:>3}. {row['score']:.3f}  {str(row['title'])[:90]}")


if __name__ == "__main__":
  main()
