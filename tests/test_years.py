import pandas as pd

from litmap import years


def _records(*rows):
  """(record id, year, type, authors, title) per row, as dedup's merged table."""

  return pd.DataFrame([
    {"record_id": record_id, "year": year, "first_published": f"{year}-07-24", "pub_types": pub_types,
     "authors": authors, "title": title}
    for record_id, year, pub_types, authors, title in rows
  ]).astype({"year": "Int64"})


THESIS = (
  "openalex:W1", 2026, "dissertation; repository", "Alessandra Mecozzi Saha",
  "Effects of synchronous music on psychological responses among elite triathletes and runners",
)

ARTICLE = (
  "pubmed:2", 2012, "Journal Article", "Terry PC, Karageorghis CI, Saha AM",
  "Effects of synchronous music on treadmill running among elite triathletes.",
)


def test_a_re_registered_thesis_gets_the_year_of_its_related_works():
  checked, checks = years.check_years(_records(THESIS, ARTICLE), search_year=2026)
  thesis = checked.iloc[0]

  assert (thesis["year"], thesis["year_reported"]) == (2012, 2026)
  assert thesis["year_flag"] == "later_than_related_works"
  assert pd.isna(thesis["first_published"])  # from the same registration as the wrong year

  assert checks == [{
    "record_id": "openalex:W1",
    "flag": "later_than_related_works",
    "reported_year": 2026,
    "year": 2012,
    "source": "related_works",
    "related_works": [
      {"record_id": "pubmed:2", "year": 2012, "shared_authors": ["saha"], "shared_title_words": "5 of 7"},
    ],
    "cleared_first_published": "2026-07-24",
  }]


def test_without_related_works_a_current_year_record_is_flagged_but_keeps_its_year():
  other_author = (*ARTICLE[:3], "Lee A", ARTICLE[4])

  checked, checks = years.check_years(_records(THESIS, other_author), search_year=2026)

  assert (checked.iloc[0]["year"], checked.iloc[0]["year_flag"]) == (2026, "current_year")
  assert (checks[0]["source"], checks[0]["related_works"]) == ("reported", [])


def test_journal_articles_and_years_close_to_related_works_are_not_flagged():
  thesis_2013 = (THESIS[0], 2013, *THESIS[2:])  # a year after its article: normal for a thesis
  current_article = (ARTICLE[0], 2026, *ARTICLE[2:])

  assert years.check_years(_records(thesis_2013, ARTICLE), search_year=2026)[1] == []
  assert years.check_years(_records(current_article), search_year=2026)[1] == []


def test_surnames_match_across_pubmed_and_openalex_name_styles():
  pubmed, openalex = years.surnames("Van Dyck E, Terry PC"), years.surnames("Edith Van Dyck, Peter C. Terry")

  assert pubmed == openalex == {"dyck", "terry"}


def test_the_search_year_comes_from_the_run_id():
  assert years.run_year("20261024T214522Z") == 2026
