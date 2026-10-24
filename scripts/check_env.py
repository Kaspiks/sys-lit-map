"""Smoke test: Python packages, system tools, API keys, and one tiny query per database.

Run inside the container:  python scripts/check_env.py   (or just `check`)
"""

import importlib
import os
import shutil
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv("/workspace/.env", override=True)
console = Console()
EMAIL = os.getenv("CONTACT_EMAIL") or None
TEST_QUERY = "running economy"

PACKAGES = [
  "IPython", "litmap", "requests", "tenacity", "Bio", "pyalex", "habanero",
  "pandas", "numpy", "pyarrow", "rapidfuzz", "unidecode", "sklearn", "asreview",
  "rispy", "bibtexparser", "openpyxl", "matplotlib", "seaborn", "graphviz",
  "yaml", "pydantic", "dotenv",
]


def check_packages():
  table = Table(title="Python packages")
  table.add_column("package")
  table.add_column("version")
  all_ok = True

  for name in PACKAGES:
    try:
      module = importlib.import_module(name)
      table.add_row(name, str(getattr(module, "__version__", "ok")))
    except Exception as error:
      all_ok = False
      table.add_row(name, f"[red]FAIL: {error}")

  console.print(table)

  return all_ok


def check_setup():
  table = Table(title="Setup")
  table.add_column("item")
  table.add_column("status")
  table.add_row("graphviz `dot` binary", "ok" if shutil.which("dot") else "[red]missing")

  for var in ["CONTACT_EMAIL", "NCBI_API_KEY", "OPENALEX_API_KEY"]:
    table.add_row(var, "set" if os.getenv(var) else "[yellow]not set (see .env.example)")

  console.print(table)


def europepmc():
  import requests

  response = requests.get(
    "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
    params={"query": TEST_QUERY, "format": "json", "pageSize": 1},
    timeout=30,
  )

  response.raise_for_status()

  return response.json()["hitCount"]


def pubmed():
  from Bio import Entrez

  if EMAIL:
    Entrez.email = EMAIL

  Entrez.api_key = os.getenv("NCBI_API_KEY") or None

  with Entrez.esearch(db="pubmed", term=TEST_QUERY, retmax=0) as handle:
    return int(Entrez.read(handle)["Count"])


def openalex():
  import pyalex

  if EMAIL:
    pyalex.config.email = EMAIL

  if key := os.getenv("OPENALEX_API_KEY"):
    pyalex.config.api_key = key

  return pyalex.Works().search(TEST_QUERY).count()


def crossref():
  from habanero import Crossref
  cr = Crossref(mailto=EMAIL) if EMAIL else Crossref()

  return cr.works(query=TEST_QUERY, limit=1)["message"]["total-results"]


def check_apis():
  table = Table(title=f'APIs (test query: "{TEST_QUERY}")')
  table.add_column("database")
  table.add_column("hits")
  all_ok = True

  for name, count_hits in [("Europe PMC", europepmc), ("PubMed", pubmed),
                   ("OpenAlex", openalex), ("Crossref", crossref)]:
    try:
      table.add_row(name, f"{count_hits():,}")
    except Exception as error:
      all_ok = False
      table.add_row(name, f"[red]FAIL: {type(error).__name__}: {error}")

  console.print(table)

  return all_ok


if __name__ == "__main__":
  packages_ok = check_packages()
  check_setup()
  apis_ok = check_apis()
  sys.exit(0 if packages_ok and apis_ok else 1)
