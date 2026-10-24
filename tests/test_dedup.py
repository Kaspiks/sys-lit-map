import pandas as pd
import pytest

from litmap import dedup


def test_rows_linked_through_a_shared_neighbour_end_up_together():
  df = pd.DataFrame({
    "doi":  ["10.1/a", "10.1/a", None,  "10.1/b", None],
    "pmid": [None,     "111",    "111", None,     None],
  })

  uf = dedup.UnionFind(len(df))
  assert dedup.link_on(uf, df, "doi") == 1   # rows 0 and 1
  assert dedup.link_on(uf, df, "pmid") == 1  # row 2 joins them through row 1

  groups = [uf.find(i) for i in range(len(df))]
  assert groups[0] == groups[1] == groups[2]
  assert len(set(groups)) == 3  # rows 3 and 4 stay on their own


def test_title_match_respects_different_dois():
  df = pd.DataFrame({
    "doi":       ["10.1/a", "10.1/b", "10.1/c", None],
    "title_key": ["productsofinterest", "productsofinterest", "samepaper", "samepaper"],
  })

  uf = dedup.UnionFind(len(df), dois=df["doi"])
  dedup.link_on(uf, df, "title_key", guard=True)

  assert uf.find(0) != uf.find(1)  # same title, different DOIs: different works
  assert uf.find(2) == uf.find(3)  # same title, one DOI missing: same work


def test_doi_prefixes_and_case_are_normalised():
  assert dedup.normalise_doi("https://doi.org/10.1/ABC ") == "10.1/abc"
  assert dedup.normalise_doi("doi:10.1/abc") == "10.1/abc"
  assert dedup.normalise_doi("not a doi") is None


def _fuzzy_case(dois, titles, years):
  df = dedup.add_keys(pd.DataFrame({"doi": dois, "title": titles, "year": years}))
  uf = dedup.UnionFind(len(df), dois=df["doi"])

  return dedup.link_fuzzy_titles(uf, df)


def test_fuzzy_title_joins_a_typo_when_one_record_has_no_doi():
  titles = ["Spontaneous entrainment of running cadence to music tempo",
            "Spontaneous entrainment of running cadense to music tempo"]

  assert _fuzzy_case(["10.1/a", None], titles, [2015, 2015]) == 1


@pytest.mark.parametrize("dois, titles, years", [
  # different DOIs
  (["10.1/a", "10.1/b"], ["Products of interest", "Products of interest"], [2021, 2022]),
  # numbers differ
  (
    [None, None],
    ["Metabolic calculations in action part 1", "Metabolic calculations in action part 2"],
    [2020, 2020],
  ),
  # years too far apart
  ([None, None], ["Effects of music on running", "Effects of music on running"], [2010, 2015]),
])
def test_fuzzy_title_refuses_different_works(dois, titles, years):
  assert _fuzzy_case(dois, titles, years) == 0


def _two_copies_of_one_paper():
  return pd.DataFrame({
    "database": ["openalex", "pubmed"], "source_id": ["W1", "123"],
    "pmid": [None, "123"], "doi": ["10.1/a", None],
    "title": ["A title", "A title."], "abstract": ["short", "a much longer abstract"],
    "year": [2020, 2021], "first_published": ["2020-11-18", "2020-11-18"],
    "journal": [None, "A journal"], "authors": ["Ann Lee", "Lee A"],
    "language": ["en", "en"], "pub_types": ["article", "Journal Article"],
  })


def test_merge_keeps_the_richest_metadata_and_a_canonical_id():
  merged, _ = dedup.merge_groups(_two_copies_of_one_paper(), [0, 0])
  row = merged.iloc[0]
  assert row.record_id == "pubmed:123"            # PubMed outranks OpenAlex
  assert row.doi == "10.1/a"                      # gap filled from OpenAlex
  assert row.abstract == "a much longer abstract"
  assert row.databases == "openalex+pubmed" and row.n_records == 2


def test_record_ids_survive_a_rerun():
  previous = pd.DataFrame({"record_id": ["openalex:W1"], "member_ids": ["openalex:W1"]})
  merged, retired = dedup.merge_groups(_two_copies_of_one_paper(), [0, 0], previous=previous)
  assert merged.iloc[0].record_id == "openalex:W1"  # kept, although PubMed would now be canonical
  assert retired == []
