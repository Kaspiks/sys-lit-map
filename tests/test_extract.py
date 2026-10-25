import json

import pandas as pd
import pytest
from openpyxl import load_workbook

from litmap import common, extract
from litmap.config import FormField

FORM = (
  FormField("full_text", "choice", options=("include", "exclude")),
  FormField("full_text_reason", "choice", options=("not_running", "other"), required_for=("exclude",)),
  FormField("n_participants", "integer", required_for=("include",)),
  FormField("age_mean", "number"),
  FormField("stimulus", "choices", options=("music", "metronome"), required_for=("include",)),
  FormField("notes", "text"),
)


def _records(*record_ids):
  return pd.DataFrame({
    "authors": ["Van Dyck E, Moens B", None, "Bood RJ"][:len(record_ids)],
    "year": [2015, 2013, 2013][:len(record_ids)],
    "title": [f"Study {record_id}" for record_id in record_ids],
    "journal": ["J"] * len(record_ids),
    "doi": ["10.1/a", None, None][:len(record_ids)],
    "pmid": ["1", "2", None][:len(record_ids)],
    "screening": ["include"] * len(record_ids),
  }, index=pd.Index(list(record_ids), name="record_id"))


def _fill(path, values):
  """Type values into the sheet the way you would in Excel: {(record id, column): value}."""
  workbook = load_workbook(path)
  sheet = workbook[extract.SHEET]
  header = [cell.value for cell in sheet[1]]

  for row in sheet.iter_rows(min_row=2):
    for (record_id, column), value in values.items():
      if row[0].value == record_id:
        row[header.index(column)].value = value

  workbook.save(path)


@pytest.fixture(autouse=True)
def folders(tmp_path, monkeypatch):
  paths = {"ROOT": tmp_path, "PROCESSED_DIR": tmp_path / "processed", "LOG_DIR": tmp_path / "logs"}

  for name, path in paths.items():
    monkeypatch.setattr(common, name, path)


def test_the_template_has_citations_and_dropdowns():
  extract.template(_records("pubmed:1", "pubmed:2"), FORM)

  workbook = load_workbook(extract.sheet_path())
  sheet = workbook[extract.SHEET]
  dropdowns = {rule.formula1 for rule in sheet.data_validations.dataValidation}

  assert [cell.value for cell in sheet[2]][:7] == [
    "pubmed:1", "include", "Van Dyck E", 2015, "Study pubmed:1", "J", "https://doi.org/10.1/a",
  ]

  assert sheet["G3"].value == "https://pubmed.ncbi.nlm.nih.gov/2/"  # no DOI: the PubMed page
  assert dropdowns == {'"include,exclude"', '"not_running,other"'}
  assert extract.FORM_SHEET in workbook.sheetnames


def test_rerunning_the_template_keeps_your_work_and_adds_new_studies():
  extract.template(_records("pubmed:1"), FORM)
  _fill(extract.sheet_path(), {("pubmed:1", "notes"): "read twice"})

  sheet = extract.template(_records("pubmed:1", "pubmed:2"), FORM)

  assert list(sheet["record_id"]) == ["pubmed:1", "pubmed:2"]
  assert extract.read_sheet(extract.sheet_path()).loc[0, "notes"] == "read twice"
  assert extract.sheet_path().with_suffix(".previous.xlsx").exists()


def test_check_finds_each_kind_of_mistake_and_skips_unread_studies():
  extract.template(_records("pubmed:1", "pubmed:2", "pubmed:3"), FORM)

  _fill(extract.sheet_path(), {
    ("pubmed:1", "full_text"): "include",
    ("pubmed:1", "n_participants"): "twenty",
    ("pubmed:1", "age_mean"): "24.5",
    ("pubmed:1", "stimulus"): "music; drums",
    ("pubmed:2", "full_text"): "exclude",  # but no reason
  })

  problems = {(record_id, column) for record_id, column, _ in extract.check(form=FORM)}

  assert problems == {
    ("pubmed:1", "n_participants"),  # not a number
    ("pubmed:1", "stimulus"),  # "drums" isn't an option
    ("pubmed:2", "full_text_reason"),  # required for exclusions
  }  # pubmed:3 has no decision yet: still to read, not wrong


def test_export_writes_a_typed_table_and_refuses_while_problems_remain():
  extract.template(_records("pubmed:1", "pubmed:2"), FORM)
  _fill(extract.sheet_path(), {("pubmed:1", "full_text"): "include", ("pubmed:1", "stimulus"): "music"})

  assert extract.export(FORM) is None  # n_participants is still missing

  _fill(extract.sheet_path(), {
    ("pubmed:1", "n_participants"): 20,
    ("pubmed:1", "stimulus"): "music;metronome",
  })
  table = extract.export(FORM)
  logged = json.loads((common.LOG_DIR / "extraction.jsonl").read_text().splitlines()[-1])

  assert list(table["record_id"]) == ["pubmed:1"]  # pubmed:2 isn't decided yet
  assert table.loc[0, "n_participants"] == 20 and str(table["n_participants"].dtype) == "Int64"
  assert table.loc[0, "stimulus"] == "music; metronome"
  assert (logged["event"], logged["included"], logged["still_to_read"]) == ("export", 1, 1)
