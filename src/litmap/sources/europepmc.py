"""Europe PMC adapter. Fields, years and languages all go inside the query text."""
import re

from litmap import common
from litmap.config import scope_limits
from litmap.sources import http
from litmap.sources.query import ISO_LANGUAGES, MEDLINE_LANGUAGES, build_query, check_query

URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MAX_PAGE_SIZE = 1000

# config `search_in` -> Europe PMC field (None = all fields, including full text)
FIELDS = {"title_abstract": "TITLE_ABS", "title": "TITLE", "all": None}

# Europe PMC keeps HTML in titles and abstracts
HEADING_TAG = re.compile(r"<(h\d|title)>(.*?)</\1>", re.IGNORECASE)
INLINE_TAG = re.compile(r"</?(?:i|b|u|em|strong|sup|sub|span|sc)\b[^<>]*>", re.IGNORECASE)
BLOCK_TAG = re.compile(r"</?(?:p|br|div)\b[^<>]*/?>", re.IGNORECASE)


def make_query(cfg, filters=False):
  """The config as one Europe PMC query string."""
  query = build_query(cfg["concepts"])
  field = FIELDS[cfg.get("search_in", "title_abstract")]

  if field:
    query = f"{field}:({query})"

  if not filters:
    return query

  limits = scope_limits(cfg)

  if limits.languages:
    languages = " OR ".join(f"LANG:{MEDLINE_LANGUAGES[code]}" for code in limits.languages)
    query += f" AND ({languages})"

  if limits.year_from or limits.year_to:
    # FIRST_PDATE, not PUB_YEAR: PUB_YEAR is the journal-issue year, so a paper
    # published online in 2025 but printed in a 2026 issue would be dropped
    start = f"{limits.year_from}-01-01" if limits.year_from else "*"
    end = f"{limits.year_to}-12-31" if limits.year_to else "*"
    query += f" AND FIRST_PDATE:[{start} TO {end}]"

  return query


def search(cfg, max_results=None, filters=False):
  """Run the config's search: (total hit count, full records)."""

  return fetch(make_query(cfg, filters=filters), max_results=max_results)


def fetch(query, max_results=None, page_size=MAX_PAGE_SIZE, **extra):
  """Return (total hit count, full records) for a query string, following every page.

  Extra Europe PMC parameters pass through, e.g. sort="CITED desc".
  """

  check_query(query)

  params = {
    "query": query,
    "format": "json",
    "resultType": "core",  # "core" includes abstracts, "lite" doesn't
    "pageSize": min(page_size, max_results or page_size),
    **extra,
  }

  def fetch_page(cursor):
    data = http.get_json(URL, {**params, "cursorMark": cursor})

    return data["resultList"]["result"], data["hitCount"], data.get("nextCursorMark")

  return http.collect_pages(fetch_page, max_results)


def to_frame(records):
  """Flatten Europe PMC records into the common record table."""

  return common.records_frame(_parse_record(record) for record in records)


def _parse_record(record):
  journal = (record.get("journalInfo") or {}).get("journal") or {}
  pub_year = record.get("pubYear", "")
  language = record.get("language")

  return {
    "database": "europepmc",
    "source_id": f'{record["source"]}:{record["id"]}',  # ids are only unique per source (MED, PPR...)
    "pmid": record.get("pmid"),
    "doi": (record.get("doi") or "").lower() or None,
    "title": _strip_html(record.get("title")),
    "abstract": _strip_html(record.get("abstractText")),
    "year": int(pub_year) if pub_year.isdigit() else None,
    "first_published": record.get("firstPublicationDate"),
    "journal": journal.get("title") or record.get("journalTitle"),
    "authors": record.get("authorString"),
    "language": ISO_LANGUAGES.get(language, language),
    "pub_types": "; ".join((record.get("pubTypeList") or {}).get("pubType", [])),
  }


def _strip_html(text):
  """'<h4>Methods</h4>We <i>ran</i>' -> 'Methods: We ran'.

  Only known tags are removed, so text like 'p<0.05 ... >' survives.
  """

  if not text:
    return text

  text = HEADING_TAG.sub(r" \2: ", text)
  text = INLINE_TAG.sub("", text)
  text = BLOCK_TAG.sub(" ", text)

  return re.sub(r"\s+", " ", text).strip()
