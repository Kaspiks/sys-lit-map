"""Stage 3: screening, step 2: ranker (recall-oriented screening prioritisation).

One iteration trains on every decision logged so far and ranks the records not screened yet,
most likely relevant first. Planned (docs/architecture.md): a random forest on TF-IDF of title
and abstract plus metadata features, compared against logistic regression, retrained after
each batch of decisions.

After the seed set, the screening server calls retrain_if_due() after every decision, so a
new iteration runs after each batch (ranker.batch_size in screening.yaml) and decides the order
of the records still to screen. Every iteration is saved and logged with everything replay()
needs to rebuild it.

Run one iteration:        python -m litmap.rank
Replay logged iterations: python -m litmap.rank --replay      (or --replay 3 for one)
Step through it:          debug -m litmap.rank, then in VS Code: Run and Debug -> "Attach to container"
Scores are for you and the evaluation only; the screening screen never shows them.
"""

import argparse
import re
from typing import NamedTuple

import joblib
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from litmap import common, dedup, screening
from litmap.concepts import ConceptTerms
from litmap.config import load_config, load_screening_settings

RELEVANT = 1  # include or maybe: a "maybe" still has to be read in full, so the ranker should find them
IRRELEVANT = 0
DEFAULT_MIN_LABELS = {"relevant": 5, "irrelevant": 5}
DEFAULT_MODEL = "random_forest"
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


def main(settings=None, concepts=None, verbose=True):
  """One ranker iteration. Returns the ranking, or None while there are too few labels.
  """

  settings = settings or load_screening_settings()
  concepts = concepts or load_config()["concepts"]
  labelled, unlabelled = split_by_label(screening.Workspace.load(settings=settings))

  if not enough_labels(labelled, settings.ranker):
    return None

  features = build_features(labelled, unlabelled, settings.ranker, concepts)
  model = train(features.labelled, labelled["label"], settings.ranker, seed=settings.seed)
  ranking = rank(unlabelled, score(model, features.unlabelled))
  save_iteration(model, ranking, labelled, settings, concepts)

  if verbose:
    print_top(ranking)

  return ranking


def retrain_if_due():
  """Run an iteration when screening needs one (see Workspace.ranker_due). True if it ran."""

  if not screening.Workspace.load().ranker_due():
    return False

  return main(verbose=False) is not None


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
  """Fit the model named in ranker_settings["model"] (random forest by default) and return it.

  logistic_regression is the comparison from the plan. random_state=seed makes the same
  decisions always give the same ranking, which is what replay() checks.
  """

  model_name = ranker_settings.get("model", DEFAULT_MODEL)

  if model_name not in MODELS:
    raise ValueError(f"Unknown model {model_name!r}; choose from {list(MODELS)}")

  model = MODELS[model_name](ranker_settings, seed)
  model.fit(features, labels.astype(int))

  return model


def score(model, features):
  """The probability of being relevant, one per record."""
  relevant_column = list(model.classes_).index(RELEVANT)

  return model.predict_proba(features)[:, relevant_column]


def random_forest(ranker_settings, seed):
  return RandomForestClassifier(
    n_estimators=ranker_settings.get("n_estimators", 100),
    class_weight=ranker_settings.get("class_weight", "balanced"),
    random_state=seed,
  )


def logistic_regression(ranker_settings, seed):
  return LogisticRegression(
    class_weight=ranker_settings.get("class_weight", "balanced"),
    max_iter=1000,
    random_state=seed,
  )


MODELS = {"random_forest": random_forest, "logistic_regression": logistic_regression}


def rank(unlabelled, scores):
  """Records not screened yet, most likely relevant first.

  Ties go by record id, so the same scores always give the same order.
  """

  ranking = unlabelled.assign(score=scores).reset_index()

  return ranking.sort_values(["score", "record_id"], ascending=[False, True], ignore_index=True)


def save_iteration(model, ranking, labelled, settings, concepts):
  """Save this iteration's model and ranking, and log everything replay() needs. Returns its number."""
  iteration = 1 + len(_rank_events())
  name = f"iteration_{iteration:03d}"
  model_path = common.MODELS_DIR / f"{name}.joblib"
  ranking_path = common.INTERIM_DIR / "rankings" / f"{name}.parquet"
  pool_path = common.INTERIM_DIR / "prefiltered.parquet"

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
    "concepts": concepts,
    "pool_file": common.relative(pool_path),
    "pool_sha256": common.sha256(pool_path),
    "training": [[record_id, int(label)] for record_id, label in labelled["label"].items()],
    "model_file": common.relative(model_path),
    "model_sha256": common.sha256(model_path),
    "ranking_file": common.relative(ranking_path),
    "ranking_sha256": common.sha256(ranking_path),
  })

  print(f"Iteration {iteration}: trained on {len(labelled)} decisions, ranked {len(ranking)} records")

  return iteration


def replay(iteration):
  """Rebuild a logged iteration from its logged inputs. True if it gives the same ranking.

  Raises ValueError when it can't be replayed: logged before replay information was kept,
  or the screening pool changed since.
  """

  event = _rank_event(iteration)

  if "pool_sha256" not in event:
    raise ValueError(f"iteration {iteration} was logged before replay information was kept")

  if common.sha256(common.ROOT / event["pool_file"]) != event["pool_sha256"]:
    raise ValueError(f"{event['pool_file']} has changed since iteration {iteration}")

  pool = screening.load_pool()
  labels = dict(event["training"])
  is_labelled = pool.index.isin(list(labels))
  labelled = pool[is_labelled].assign(label=pd.Series(labels, dtype="Int64"))
  unlabelled = pool[~is_labelled]

  features = build_features(labelled, unlabelled, event["ranker"], event["concepts"])
  model = train(features.labelled, labelled["label"], event["ranker"], seed=event["seed"])
  replayed = rank(unlabelled, score(model, features.unlabelled))
  saved = pd.read_parquet(common.ROOT / event["ranking_file"])

  same_order = list(replayed["record_id"]) == list(saved["record_id"])

  return same_order and np.allclose(replayed["score"], saved["score"])


def print_top(ranking, count=TOP_TO_PRINT):
  """The highest-scored records, to check the ranker while you develop it."""

  for position, row in ranking.head(count).iterrows():
    print(f"{position + 1:>3}. {row['score']:.3f}  {str(row['title'])[:90]}")


def _rank_events():
  return [event for event in common.read_log("screening") if event.get("event") == "rank"]


def _rank_event(iteration):
  matching = [event for event in _rank_events() if event["iteration"] == iteration]

  if not matching:
    raise ValueError(f"No iteration {iteration} in logs/screening.jsonl")

  return matching[-1]


def _replay_report(iteration):
  try:
    reproduced = replay(iteration)
  except ValueError as error:
    return f"iteration {iteration}: can't replay ({error})"

  return f"iteration {iteration}: {'reproduced exactly' if reproduced else 'DIFFERENT ranking'}"


def _command_line():
  parser = argparse.ArgumentParser(description="Run one ranker iteration, or replay logged ones.")

  parser.add_argument("--replay", nargs="?", const="all", metavar="ITERATION",
                      help="rebuild logged iterations (all, or one number) and check their rankings")

  arguments = parser.parse_args()

  if arguments.replay is None:
    main()

    return

  if arguments.replay == "all":
    iterations = [event["iteration"] for event in _rank_events()]
  else:
    iterations = [int(arguments.replay)]

  for iteration in iterations:
    print(_replay_report(iteration))


if __name__ == "__main__":
  _command_line()
