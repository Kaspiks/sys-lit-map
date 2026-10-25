"""Stage 2, after merging: publication years that are probably wrong.

Repositories re-register old theses and papers under new DOIs, and OpenAlex then dates the
work by the new registration: a 2012 PhD thesis shows as 2026. A wrong year can make the year
rule exclude a record, and it puts the record in the wrong year in the report.
"""

import re

import pandas as pd
from unidecode import unidecode

from litmap import common

SUSPECT_TYPES = re.compile(r"dissertation|thesis|repository", re.IGNORECASE)
YEAR_TOLERANCE = 1  # an online-first year can be one before the print year, as in dedup
RELATED_TITLE_SHARE = 0.5

STOPWORDS = frozenset(
  "a an and are as at by for from in into is of on or the to via vs versus with among".split()
)


def check_years(records, search_year):
  records = records.assign(
    year_reported=pd.Series(pd.NA, index=records.index, dtype="Int64"),
    year_flag=pd.Series(pd.NA, index=records.index, dtype="string"),
  )

  people = records["authors"].map(surnames)
  words = records["title"].map(title_words)
  suspects = records.index[records["pub_types"].fillna("").str.contains(SUSPECT_TYPES)]
  checks = []

  for index in suspects:
    year = common.plain(records.at[index, "year"])

    if year is None:
      continue

    related = related_works(index, records, people, words)
    latest = max((work["year"] for work in related), default=None)
    flag = year_flag(year, latest, search_year)

    if flag is None:
      continue

    check = {
      "record_id": records.at[index, "record_id"],
      "flag": flag,
      "reported_year": year,
      "year": year,
      "source": "reported",
      "related_works": related,
    }

    if flag == "later_than_related_works":
      check.update(year=latest, source="related_works")
      check["related_works"] = [work for work in related if work["year"] == latest]
      check |= _clear_first_published(records, index, year)
      records.at[index, "year"] = latest

    records.at[index, "year_reported"] = year
    records.at[index, "year_flag"] = flag
    checks.append(check)

  return records, checks


def run_year(run_name):
  """The year a search run was made: run ids start with it (20261024T214522Z). Otherwise this year."""
  match = re.match(r"\d{4}", run_name)

  return int(match.group()) if match else int(common.now()[:4])


def year_flag(year, latest_related_year, search_year):
  """Why a dissertation's or repository item's year is doubtful, or None if it isn't."""

  if latest_related_year is not None and year > latest_related_year + YEAR_TOLERANCE:
    return "later_than_related_works"

  if year == search_year:
    return "current_year"

  return None


def related_works(index, records, people, words):
  """Other dated records that share an author's surname and at least half the shorter title's words."""
  related = []

  for other in records.index:
    year = common.plain(records.at[other, "year"])
    shared_people = people[index] & people[other]
    shared_words = words[index] & words[other]
    shorter = min(len(words[index]), len(words[other]))

    if other == index or year is None or not shared_people or not shorter:
      continue

    if len(shared_words) >= RELATED_TITLE_SHARE * shorter:
      related.append({
        "record_id": records.at[other, "record_id"],
        "year": int(year),
        "shared_authors": sorted(shared_people),
        "shared_title_words": f"{len(shared_words)} of {shorter}",
      })

  return related


def surnames(authors):
  """{'saha', 'terry'} from 'Terry PC, Saha AM' (PubMed) or 'Alessandra Mecozzi Saha' (OpenAlex)."""
  found = set()

  for name in str(authors or "").split(", "):
    tokens = unidecode(name).replace(".", " ").split()

    if len(tokens) > 1 and tokens[-1].isupper() and len(tokens[-1]) <= 3:
      tokens = tokens[:-1]  # PubMed puts the initials last

    if tokens:
      found.add(tokens[-1].lower())

  return found


def title_words(title):
  words = re.findall(r"[a-z0-9]+", unidecode(str(title or "")).lower())

  return {word for word in words if word not in STOPWORDS}


def _clear_first_published(records, index, reported_year):
  """The publication date came from the same registration as the wrong year: clear it, and say so."""
  first_published = common.plain(records.at[index, "first_published"])

  if first_published is None or not str(first_published).startswith(str(reported_year)):
    return {}

  records.at[index, "first_published"] = None

  return {"cleared_first_published": str(first_published)}
