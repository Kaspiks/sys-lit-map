"""Stage 5: data extraction (charting) from the full texts.

The values come from reading each study, so you fill them in; this module makes that quick
and checkable. The form, every field with its type and allowed answers, is config/extraction.yaml.

  template()  writes data/processed/extraction.xlsx: one row per study you included or marked
              maybe in screening, with its citation and link filled in, a dropdown for every
              one-answer field, and the form on a second sheet. Rerunning it adds newly included
              studies and keeps everything you've filled in (the old file is kept as a backup).
  check()     lists every problem in the filled sheet: an option that doesn't exist, text where
              a number belongs, a required answer left empty.
  export()    writes the rows that have a full-text decision to data/processed/extraction.csv,
              typed (numbers as numbers), and logs it. It refuses while check() finds problems.

Run: python -m litmap.extract template | check | export
"""

import argparse
import shutil

import pandas as pd
from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation

from litmap import common, screening
from litmap.config import EXTRACTION_CONFIG_PATH, load_extraction_form

SHEET = "Extraction"
FORM_SHEET = "Form"
DECISION = "full_text"  # the form field holding the full-text decision
CITATION_COLUMNS = ["record_id", "screening", "first_author", "year", "title", "journal", "link"]
SEPARATOR = ";"  # between the answers of a choices field
DROPDOWN_ROWS = 1000
COLUMN_WIDTHS = {"title": 60, "link": 32, "text": 40}
DEFAULT_WIDTH = 16


def template(records=None, form=None):
  """Write the extraction sheet: newly included studies added, filled-in values kept."""
  records = included_records() if records is None else records
  form = form or load_extraction_form()
  path = sheet_path()

  existing = read_sheet(path) if path.exists() else pd.DataFrame(columns=["record_id"])
  citations = citation_rows(records)
  added = citations[~citations["record_id"].isin(existing["record_id"])]
  sheet = pd.concat([existing, added], ignore_index=True)
  sheet = sheet.reindex(columns=_columns(sheet, form))

  if path.exists():
    shutil.copy2(path, path.with_suffix(".previous.xlsx"))  # a slip while editing can't cost your work

  _write_workbook(sheet, form, path)

  no_longer_included = sorted(set(existing["record_id"]) - set(records.index))

  common.append_log("extraction", {
    "event": "template",
    "timestamp": common.now(),
    "rows": len(sheet),
    "added": list(added["record_id"]),
    "no_longer_included": no_longer_included,
    "sheet_file": common.relative(path),
    "sheet_sha256": common.sha256(path),
  })

  print(f"{len(sheet)} studies in {common.relative(path)} ({len(added)} new)")

  if no_longer_included:
    print(f"Kept, but no longer included in screening: {', '.join(no_longer_included)}")

  return sheet


def check(sheet=None, form=None):
  """Every problem in the filled sheet, as [(record id, column, message)]. Prints them too."""
  form = form or load_extraction_form()
  sheet = read_sheet(sheet_path()) if sheet is None else sheet
  problems = [problem for _, row in sheet.iterrows() for problem in row_problems(row, form)]
  to_do = sum(_value(decision) is None for decision in sheet[DECISION])

  for record_id, column, message in problems:
    print(f"{record_id}  {column}: {message}")

  print(f"{len(problems)} problems; {len(sheet) - to_do} studies decided, {to_do} still to read")

  return problems


def export(form=None):
  """Write the decided rows to data/processed/extraction.csv and log it. None while problems remain."""
  form = form or load_extraction_form()
  path = sheet_path()
  sheet = read_sheet(path)

  if check(sheet, form):
    print("Nothing exported: fix the problems above first.")

    return None

  decided = sheet[sheet[DECISION].map(_value).notna()]
  table = typed(decided, form)
  out_path = common.PROCESSED_DIR / "extraction.csv"
  table.to_csv(out_path, index=False)
  excluded = table[table[DECISION] == "exclude"]

  common.append_log("extraction", {
    "event": "export",
    "timestamp": common.now(),
    "form_sha256": common.sha256(EXTRACTION_CONFIG_PATH),
    "sheet_file": common.relative(path),
    "sheet_sha256": common.sha256(path),
    "output_file": common.relative(out_path),
    "output_sha256": common.sha256(out_path),
    "included": int((table[DECISION] == "include").sum()),
    "excluded": excluded["full_text_reason"].value_counts().to_dict() if "full_text_reason" in table else {},
    "still_to_read": len(sheet) - len(decided),
  })

  print(f"{len(table)} studies -> {common.relative(out_path)}")

  return table


def included_records():
  """Pool records you included or marked maybe in screening: both go to the full-text check."""
  workspace = screening.Workspace.load()

  labels = {
    record_id: decision["label"]
    for record_id, decision in workspace.decisions.items()
    if decision["label"] in screening.RELEVANT_LABELS
  }

  return workspace.pool.loc[list(labels)].assign(screening=pd.Series(labels))


def citation_rows(records):
  """The pre-filled part of each row: which study it is, where to read it, how screening judged it."""
  first_authors = records["authors"].fillna("").str.split(", ").str[0]

  return pd.DataFrame({
    "record_id": records.index,
    "screening": records["screening"],
    "first_author": first_authors.replace("", None),
    "year": records["year"],
    "title": records["title"],
    "journal": records["journal"],
    "link": [_link(doi, pmid) for doi, pmid in zip(records["doi"], records["pmid"])],
  }).reset_index(drop=True)


def row_problems(row, form):
  """Problems in one study's row. A row without a full-text decision is still to read, not wrong."""
  decision = _value(row.get(DECISION))

  if decision is None:
    return []

  problems = []

  for field in form:
    value = _value(row.get(field.name))

    if value is None:
      if decision in field.required_for:
        problems.append((row["record_id"], field.name, f"needed for every {decision}d study"))

      continue

    message = value_problem(field, value)

    if message:
      problems.append((row["record_id"], field.name, message))

  return problems


def value_problem(field, value):
  """What's wrong with one filled-in value, or None."""

  if field.type == "integer" and not _is_number(value, whole=True):
    return f"{value!r} should be a whole number"

  if field.type == "number" and not _is_number(value):
    return f"{value!r} should be a number"

  if field.type == "choice" and value not in field.options:
    return f"{value!r} isn't an option; use one of {', '.join(field.options)}"

  unknown = [item for item in _items(value) if item not in field.options] if field.type == "choices" else []

  if unknown:
    return f"{', '.join(unknown)} isn't an option; use {', '.join(field.options)}, separated by {SEPARATOR}"

  return None


def typed(sheet, form):
  """The sheet with numbers as numbers and several answers tidied to "a; b"."""
  table = sheet.copy()

  for field in form:
    if field.type == "integer":
      table[field.name] = pd.to_numeric(table[field.name]).astype("Int64")
    elif field.type == "number":
      table[field.name] = pd.to_numeric(table[field.name])
    elif field.type == "choices":
      table[field.name] = table[field.name].map(lambda value: f"{SEPARATOR} ".join(_items(value)) or None)

  return table


def read_sheet(path):
  """The extraction sheet as text, exactly as you left it."""

  return pd.read_excel(path, sheet_name=SHEET, dtype=str)


def sheet_path():
  return common.PROCESSED_DIR / "extraction.xlsx"


def _columns(sheet, form):
  """Citation columns, then the form's fields in form order, then any columns you added yourself."""
  known = CITATION_COLUMNS + [field.name for field in form]

  return known + [column for column in sheet.columns if column not in known]


def _write_workbook(sheet, form, path):
  workbook = Workbook()
  rows = workbook.active
  rows.title = SHEET
  rows.append(list(sheet.columns))

  for values in sheet.itertuples(index=False):
    rows.append([common.plain(value) for value in values])

  rows.freeze_panes = "C2"  # keep the header and the record id in view while scrolling
  fields = {field.name: field for field in form}

  for position, column in enumerate(sheet.columns, start=1):
    letter = rows.cell(row=1, column=position).column_letter
    field = fields.get(column)
    rows.column_dimensions[letter].width = COLUMN_WIDTHS.get(column) or _width(field)

    if field and field.type == "choice":
      dropdown = DataValidation(type="list", formula1=f'"{",".join(field.options)}"', allow_blank=True)
      dropdown.add(f"{letter}2:{letter}{DROPDOWN_ROWS}")
      rows.add_data_validation(dropdown)

  _write_form_sheet(workbook.create_sheet(FORM_SHEET), form)
  path.parent.mkdir(parents=True, exist_ok=True)
  workbook.save(path)


def _write_form_sheet(sheet, form):
  """The form itself, next to the data: what each column means and which answers it takes."""
  sheet.append(["column", "type", "required for", "options", "meaning"])

  for field in form:
    options = f"{SEPARATOR} ".join(field.options)
    sheet.append([field.name, field.type, ", ".join(field.required_for), options, field.label])

  for letter, width in zip("ABCDE", (22, 10, 14, 60, 60)):
    sheet.column_dimensions[letter].width = width


def _width(field):
  return COLUMN_WIDTHS["text"] if field and field.type == "text" else DEFAULT_WIDTH


def _link(doi, pmid):
  """Where to read the full text: the DOI, else the PubMed page."""

  if common.plain(doi):
    return f"https://doi.org/{doi}"

  if common.plain(pmid):
    return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

  return None


def _value(value):
  """A filled-in cell as stripped text; empty cells become None."""
  value = common.plain(value)
  text = str(value).strip() if value is not None else ""

  return text or None


def _items(value):
  return [item.strip() for item in str(value or "").split(SEPARATOR) if item.strip()]


def _is_number(value, whole=False):
  try:
    number = float(value)
  except ValueError:
    return False

  return number.is_integer() if whole else True


def _command_line():
  parser = argparse.ArgumentParser(description="The data-extraction sheet: create it, check it, export it.")
  parser.add_argument("step", choices=["template", "check", "export"])
  step = parser.parse_args().step
  {"template": template, "check": check, "export": export}[step]()


if __name__ == "__main__":
  _command_line()
