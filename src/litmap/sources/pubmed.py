"""PubMed adapter (NCBI E-utilities): esearch finds the PMIDs, efetch downloads the records."""
import os
import re
import time
import xml.etree.ElementTree as ET

from litmap import common
from litmap.config import scope_limits
from litmap.sources import http
from litmap.sources.query import ISO_LANGUAGES, LANGUAGE_NAMES, build_query

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
MAX_IDS = 10_000  # esearch returns at most this many IDs for one query
FETCH_BATCH_SIZE = 200

# NCBI allows 10 requests per second with an API key, 3 without
DELAY_WITH_KEY = 0.1
DELAY_WITHOUT_KEY = 0.34

MONTHS = {
  name: number
  for number, name in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
  )
}


def make_query(cfg, filters=False):
  """The config as PubMed wants it: (search term, extra E-utilities params)."""
  term = build_query(cfg["concepts"])

  if not filters:
    return term, {}

  limits = scope_limits(cfg)

  if limits.languages:
    languages = " OR ".join(f"{LANGUAGE_NAMES[code]}[la]" for code in limits.languages)
    term += f" AND ({languages})"

  date_range = {}

  if limits.year_from or limits.year_to:  # E-utilities needs both ends of the range
    date_range = {"datetype": "pdat", "mindate": limits.year_from or 1800, "maxdate": limits.year_to or 3000}

  return term, date_range


def search(cfg, max_results=None, filters=False):
  """Run the config's search: (total hit count, full records as XML strings)."""
  term, date_range = make_query(cfg, filters=filters)
  hit_count, pmids = find_ids(term, retmax=min(max_results or MAX_IDS, MAX_IDS), **date_range)

  if hit_count > len(pmids) and not max_results:
    raise ValueError(f"PubMed found {hit_count} records but returns at most {MAX_IDS}; narrow the search")

  return hit_count, fetch_records(pmids)


def find_ids(term, retmax=10, **extra):
  """Return (total hit count, PMIDs) for a search term.

  Extra E-utilities parameters pass through, e.g. mindate="2010", maxdate="2025", datetype="pdat".
  """

  params = {"db": "pubmed", "term": term, "retmode": "json", "retmax": retmax, **_identity(), **extra}
  result = http.get_json(ESEARCH_URL, params)["esearchresult"]

  return int(result["count"]), result["idlist"]


def fetch_records(pmids, batch_size=FETCH_BATCH_SIZE):
  """Download the full records for a list of PMIDs: one XML string per article."""
  delay = DELAY_WITH_KEY if os.getenv("NCBI_API_KEY") else DELAY_WITHOUT_KEY
  records = []

  for start in range(0, len(pmids), batch_size):
    batch = pmids[start:start + batch_size]
    params = {"db": "pubmed", "id": ",".join(batch), "retmode": "xml", **_identity()}
    article_set = ET.fromstring(http.request("POST", EFETCH_URL, params).content)
    records += [ET.tostring(article, encoding="unicode") for article in article_set]
    time.sleep(delay)

  return records


def to_frame(records):
  """Flatten PubMed XML records into the common record table."""

  return common.records_frame(_parse_article(ET.fromstring(xml)) for xml in records)


def _identity():
  """NCBI asks every request to say who is calling; an API key raises the rate limit."""
  params = {"tool": "litmap", "email": os.getenv("CONTACT_EMAIL"), "api_key": os.getenv("NCBI_API_KEY")}

  return {key: value for key, value in params.items() if value}


def _parse_article(article):
  pmid = article.findtext(".//PMID")
  pub_date = article.find(".//PubDate")
  electronic_date = article.find(".//ArticleDate[@DateType='Electronic']")
  year = re.search(r"\d{4}", _text(pub_date) or "")  # also covers MedlineDate like "2015 Jan-Feb"
  language = article.findtext(".//Language")

  return {
    "database": "pubmed",
    "source_id": pmid,
    "pmid": pmid,
    "doi": _doi(article),
    "title": _text(article.find(".//ArticleTitle")) or _text(article.find(".//BookTitle")),
    "abstract": _abstract(article),
    "year": int(year.group()) if year else None,
    "first_published": _date(electronic_date) or _date(pub_date),
    "journal": article.findtext(".//Journal/Title"),
    "authors": ", ".join(_authors(article)),
    "language": ISO_LANGUAGES.get(language, language),
    "pub_types": "; ".join(_text(pub_type) for pub_type in article.findall(".//PublicationType")),
  }


def _doi(article):
  doi = (
    article.findtext("*/ArticleIdList/ArticleId[@IdType='doi']")
    or article.findtext(".//ELocationID[@EIdType='doi']")
  )

  return doi.lower() if doi else None


def _abstract(article):
  """Structured abstracts keep their labels: 'BACKGROUND: ... METHODS: ...'."""

  parts = [
    f"{part.get('Label')}: {_text(part)}" if part.get("Label") else _text(part)
    for part in article.findall(".//Abstract/AbstractText")
  ]

  return " ".join(parts) or None


def _authors(article):
  """'Van Dyck E' for people, the group name for collective authors."""
  names = []

  for author in article.findall(".//AuthorList/Author"):
    if author.find("LastName") is not None:
      names.append(f"{author.findtext('LastName')} {author.findtext('Initials') or ''}".strip())
    else:
      names.append(_text(author.find("CollectiveName")))

  return [name for name in names if name]


def _text(element):
  """All text inside an element, including <i>/<sup> markup that .text would cut off."""

  return "".join(element.itertext()).strip() if element is not None else None


def _date(element):
  """<Year>/<Month>/<Day> -> '2025-06-04', or '2025-06' / '2025' when parts are missing."""

  if element is None or not element.findtext("Year"):
    return None

  parts = [element.findtext("Year")]
  month = _month_number(element.findtext("Month") or "")
  day = element.findtext("Day") or ""

  if month:
    parts.append(f"{month:02d}")

    if day.isdigit():
      parts.append(f"{int(day):02d}")

  return "-".join(parts)


def _month_number(month):
  """'Apr' or '04' -> 4, anything else -> None."""
  abbreviation = month[:3].title()

  if abbreviation in MONTHS:
    return MONTHS[abbreviation]

  return int(month) if month.isdigit() else None
