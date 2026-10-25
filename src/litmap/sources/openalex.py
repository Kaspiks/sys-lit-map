"""OpenAlex adapter. The search and each filter are separate `field:value` entries."""
import os

from litmap import common
from litmap.config import scope_limits
from litmap.sources import http
from litmap.sources.query import build_query, check_query

URL = "https://api.openalex.org/works"
MAX_PAGE_SIZE = 200

# config `search_in` -> OpenAlex filter (None = all fields, via the search.exact parameter).
# Wildcards like runner* only work in the .exact (unstemmed) variants.
FIELDS = {
  "title_abstract": "title_and_abstract.search.exact",
  "title": "title.search.exact",
  "all": None,
}


def make_query(cfg, filters=False):
  """The config as OpenAlex request parameters, e.g.

  {"filter": "title_and_abstract.search.exact:(...),language:en|ru,from_publication_date:1990-01-01"}
  """

  query = build_query(cfg["concepts"])

  if "," in query:
    raise ValueError(f"Commas separate OpenAlex filters, remove them from the search terms: {query}")

  params, filter_parts = {}, []
  field = FIELDS[cfg.get("search_in", "title_abstract")]

  if field:
    filter_parts.append(f"{field}:{query}")
  else:
    params["search.exact"] = query

  if filters:
    filter_parts += _scope_filters(scope_limits(cfg))

  if filter_parts:
    params["filter"] = ",".join(filter_parts)

  return params


def search(cfg, max_results=None, filters=False):
  """Run the config's search: (total hit count, full records)."""

  return fetch(make_query(cfg, filters=filters), max_results=max_results)


def fetch(query_params, max_results=None, page_size=MAX_PAGE_SIZE, **extra):
  """Return (total hit count, full records) for query parameters, following every page.

  Paging past the first page needs OPENALEX_API_KEY (free at openalex.org/settings/api).
  Extra OpenAlex parameters pass through, e.g. sort="cited_by_count:desc".
  """

  for value in query_params.values():
    check_query(value)

  params = {
    **query_params,
    "per_page": min(page_size, max_results or page_size),
    "mailto": os.getenv("CONTACT_EMAIL"),
    "api_key": os.getenv("OPENALEX_API_KEY"),
    **extra,
  }

  params = {key: value for key, value in params.items() if value not in (None, "")}  # unset email/key

  def fetch_page(cursor):
    data = http.get_json(URL, {**params, "cursor": cursor})

    return data["results"], data["meta"]["count"], data["meta"].get("next_cursor")

  return http.collect_pages(fetch_page, max_results)


def to_frame(records):
  """Flatten OpenAlex works into the common record table."""

  return common.records_frame(_parse_work(work) for work in records)


def _scope_filters(limits):
  parts = []

  if limits.languages:
    parts.append("language:" + "|".join(limits.languages))  # | means OR within one filter

  if limits.year_from:
    parts.append(f"from_publication_date:{limits.year_from}-01-01")

  if limits.year_to:
    parts.append(f"to_publication_date:{limits.year_to}-12-31")

  return parts


def _parse_work(work):
  journal = (work.get("primary_location") or {}).get("source") or {}
  authorships = work.get("authorships") or []
  authors = [(authorship.get("author") or {}).get("display_name") for authorship in authorships]

  return {
    "database": "openalex",
    "source_id": _last_path_part(work["id"]),
    "pmid": _last_path_part((work.get("ids") or {}).get("pmid")),
    "doi": work["doi"].removeprefix("https://doi.org/").lower() if work.get("doi") else None,
    "title": work.get("title") or work.get("display_name"),
    "abstract": _rebuild_abstract(work.get("abstract_inverted_index")),
    "year": work.get("publication_year"),
    "first_published": work.get("publication_date"),
    "journal": journal.get("display_name"),
    "authors": ", ".join(name for name in authors if name),
    "language": work.get("language"),
    "pub_types": _pub_types(work.get("type"), journal.get("type")),
  }


def _pub_types(work_type, source_type):
  """The work's type, plus 'repository' when its main copy is in one (years.py checks those years)."""
  types = [work_type, "repository" if source_type == "repository" else None]

  return "; ".join(name for name in types if name) or None


def _rebuild_abstract(inverted_index):
  """OpenAlex stores abstracts as {word: [positions]}; put the words back in order."""

  if not inverted_index:
    return None

  word_at = {position: word for word, positions in inverted_index.items() for position in positions}

  return " ".join(word_at[position] for position in sorted(word_at))


def _last_path_part(url):
  """'https://openalex.org/W123' -> 'W123', 'https://pubmed.ncbi.nlm.nih.gov/456' -> '456'."""

  return url.rstrip("/").rsplit("/", 1)[-1] if url else None
