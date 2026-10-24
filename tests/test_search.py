import pandas as pd

from litmap import search


def test_validation_matches_by_doi_or_pmid():
  records = pd.DataFrame({"doi": ["10.1/a", None], "pmid": [None, "222"]})

  cfg = {"validation": [
    {"label": "A", "doi": "10.1/A"},
    {"label": "B", "pmid": "222"},
    {"label": "C", "doi": "10.1/c"},
  ]}

  result = search.check_validation(cfg, records)

  assert result["found"] == 2
  assert [study["label"] for study in result["missing"]] == ["C"]
