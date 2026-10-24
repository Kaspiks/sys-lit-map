"""Stage 4: citation chasing (snowballing) through OpenAlex.

One round takes the records you included in screening that no earlier round has chased,
collects the works they cite (backward) and the works that cite them (forward), keeps only
works the pipeline hasn't seen, and saves them as data/raw/chase/<round>/ in the same format
as a search run. dedup.deduplicate() then merges every round into the screening pool (keeping
existing record ids), rules.apply_rules() filters them, and they appear in the screening queue,
where the ranker places them. Screen them, chase again, and stop when a round brings no new
includes.

It chases through three OpenAlex calls: lookup_openalex_id (a DOI or PMID to a work id),
references (backward) and citations (forward).

Run one round:    python -m litmap.chase, then dedup.deduplicate() and rules.apply_rules()
Step through it:  debug -m litmap.chase, then in VS Code: Run and Debug -> "Attach to container"
"""

import itertools
import json
import os

import pandas as pd
import requests

from litmap import common, dedup, screening
from litmap.config import load_config
from litmap.sources import http, openalex

OPENALEX_MEMBER = "openalex:"  # member ids of records found in OpenAlex: "openalex:W123"
MAX_IDS_PER_FILTER = 100  # OpenAlex accepts up to 100 ids joined with | in one filter
DEFAULT_SEED_LABELS = ("include",)
DEFAULT_DIRECTIONS = ("backward", "forward")


def main():
  """One chasing round. Returns the new round's folder, or None when there is nothing to chase."""
  chase_settings = load_config().get("chase") or {}
  directions = chase_settings.get("directions", DEFAULT_DIRECTIONS)
  unknown = set(directions) - set(CHASERS)

  if unknown:
    raise ValueError(f"Unknown chase directions {sorted(unknown)}; choose from {list(CHASERS)}")

  seeds = seed_records(
    screening.Workspace.load(),
    chase_settings.get("seed_labels", DEFAULT_SEED_LABELS),
    common.read_log("chase"),
  )

  if seeds.empty:
    print("Nothing to chase: every included record has been chased in an earlier round.")

    return None

  seed_works = openalex_ids(seeds)
  found = {direction: CHASERS[direction](list(seed_works.values())) for direction in directions}
  new_works, found_by = unseen(found, *known_works())
  round_dir = save_round(list(seeds.index), seed_works, found, new_works, found_by)

  print(f"{len(seeds)} seeds, {len(seed_works)} known to OpenAlex. Found: "
        + ", ".join(f"{direction} {len(works)}" for direction, works in found.items())
        + f". New to the pipeline: {len(new_works)} -> {common.relative(round_dir)}")

  print("Next: dedup.deduplicate(), then rules.apply_rules(); the new records join the screening queue.")

  return round_dir


def seed_records(workspace, seed_labels, chase_events):
  """Pool records decided with one of seed_labels that no earlier round has chased."""
  chased = {record_id for event in chase_events for record_id in event.get("seeds", [])}

  seed_ids = [
    record_id for record_id, decision in workspace.decisions.items()
    if decision["label"] in seed_labels and record_id not in chased
  ]

  return workspace.pool.loc[seed_ids]


def openalex_ids(seeds):
  """{record id: OpenAlex work id ("W123")} for the seeds OpenAlex knows.

  Records the OpenAlex search found carry the id in their member ids; the others are looked
  up by DOI or PMID. Records OpenAlex doesn't know are left out, and listed.
  """

  ids, unknown = {}, []

  for record_id, record in seeds.iterrows():
    work_id = _openalex_member_id(record)

    if work_id is None:
      work_id = lookup_openalex_id(common.plain(record["doi"]), common.plain(record["pmid"]))

    if work_id:
      ids[record_id] = work_id
    else:
      unknown.append(record_id)

  if unknown:
    print(f"OpenAlex doesn't know {len(unknown)} seed(s), so they can't be chased: {', '.join(unknown)}")

  return ids


def _openalex_member_id(record):
  """'W100' when the OpenAlex search found this record ("openalex:W100" in member_ids), else None."""
  members = record["member_ids"].split(dedup.MEMBER_SEPARATOR)

  in_openalex = (
    member.removeprefix(OPENALEX_MEMBER) for member in members if member.startswith(OPENALEX_MEMBER)
  )

  return next(in_openalex, None)


def lookup_openalex_id(doi, pmid):
  """The OpenAlex work id ("W123") for a DOI or PMID, or None when OpenAlex doesn't know it.

  The identifier goes into the address, e.g. https://api.openalex.org/works/doi:10.1186/...
  or .../works/pmid:26258007; a 404 means OpenAlex doesn't know it. Either argument can be None.
  """

  params = {
    "mailto": os.getenv("CONTACT_EMAIL"),
    "api_key": os.getenv("OPENALEX_API_KEY")
  }

  params = {key: value for key, value in params.items() if value}

  for kind, value in (("doi", doi), ("pmid", pmid)):
    if not value:
      continue

    try:
      work = http.get_json(f"{openalex.URL}/{kind}:{value}", params)
    except requests.HTTPError as error:
      if error.response.status_code == 404:
        continue

      raise

    return work_id(work)

  return None


def references(work_ids):
  """Backward: the works these works cite, as raw OpenAlex works.

  Each work lists what it cites in "referenced_works". Two seeds often cite the same paper,
  so the cited ids are collected without duplicates before fetching them.
  """

  seed_works = fetch_works("openalex", work_ids)
  cited_ids = sorted({_last_part(url) for work in seed_works for url in work.get("referenced_works", [])})

  return fetch_works("openalex", cited_ids)


def citations(work_ids):
  """Forward: the works that cite these works, as raw OpenAlex works."""

  return fetch_works("cites", work_ids)


def fetch_works(filter_name, ids):
  works = []

  for batch in itertools.batched(ids, MAX_IDS_PER_FILTER):
    _, batch_works = openalex.fetch({"filter": f"{filter_name}:{'|'.join(batch)}"})
    works += batch_works

  return works


def work_id(work):
  return _last_part(work["id"])


def _last_part(url):
  return url.rstrip("/").rsplit("/", 1)[-1]


CHASERS = {"backward": references, "forward": citations}


def known_works():
  """(OpenAlex ids, DOIs) of every work the pipeline already has, so a round only adds new ones."""
  records = pd.read_parquet(common.INTERIM_DIR / "records.parquet")
  members = records["member_ids"].str.split(dedup.MEMBER_SEPARATOR).explode()
  known_ids = set(members[members.str.startswith(OPENALEX_MEMBER)].str.removeprefix(OPENALEX_MEMBER))

  for round_dir in common.chase_rounds():  # rounds not yet merged by dedup
    known_ids |= set(common.read_records(round_dir / "records.csv")["source_id"])

  return known_ids, set(records["doi"].dropna())


def unseen(found, known_ids, known_dois):
  """Works not in the pipeline yet, each once. Returns (works, {work id: directions that found it}).

  found is {direction: [raw work, ...]}; a work found both ways is kept once.
  """

  new_works, found_by = {}, {}

  for direction, works in found.items():
    for work in works:
      new_id = work_id(work)
      doi = dedup.normalise_doi(work.get("doi"))

      if new_id in known_ids or (doi and doi in known_dois):
        continue

      new_works.setdefault(new_id, work)
      found_by.setdefault(new_id, []).append(direction)

  return list(new_works.values()), found_by


def save_round(seed_ids, seed_works, found, new_works, found_by):
  """Save a round like a search run (raw JSON + records.csv), log it, and return its folder.

  Every seed is logged as chased, including those OpenAlex doesn't know, so no round retries them.
  """

  round_id = common.run_id()
  round_dir = common.RAW_DIR / "chase" / round_id
  round_dir.mkdir(parents=True)
  raw_path = round_dir / "openalex.json"
  records_path = round_dir / "records.csv"

  raw_path.write_text(json.dumps(new_works, ensure_ascii=False))
  openalex.to_frame(new_works).to_csv(records_path, index=False)

  common.append_log("chase", {
    "event": "round",
    "round": round_id,
    "timestamp": common.now(),
    "seeds": seed_ids,
    "seed_works": seed_works,
    "found": {direction: len(works) for direction, works in found.items()},
    "new_works": found_by,
    "raw_file": common.relative(raw_path),
    "raw_sha256": common.sha256(raw_path),
    "records_file": common.relative(records_path),
    "records_sha256": common.sha256(records_path),
  })

  return round_dir


if __name__ == "__main__":
  main()
