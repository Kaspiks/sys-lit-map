import pytest

from litmap.config import ScopeLimits, scope_limits
from litmap.sources import http
from litmap.sources.query import build_query, check_query


def test_build_query_ors_terms_and_ands_blocks():
  concepts = {"population": ["runner*", "running"], "exposure": ["music*"], "outcome": []}

  assert build_query(concepts) == "(runner* OR running) AND (music*)"


@pytest.mark.parametrize("query", [
  "TITLE_ABS:(running AND music",  # unbalanced bracket
  '"auditory cue AND running',  # unbalanced quote
  '"auditory cue*" AND running',  # wildcard inside a phrase
])
def test_check_query_rejects_queries_that_databases_silently_misread(query):
  with pytest.raises(ValueError):
    check_query(query)


def test_scope_limits_default_to_no_limits():
  assert scope_limits({}) == ScopeLimits()


def _serve_pages(*pages, hit_count):
  """A fetch_page function that serves `pages` in order; the cursor is the next page number."""

  def fetch_page(cursor):
    index = 0 if cursor == "*" else int(cursor)
    page = pages[index] if index < len(pages) else []

    return page, hit_count, str(index + 1)

  return fetch_page


def test_collect_pages_follows_the_cursor_until_all_results_are_in():
  fetch_page = _serve_pages([1, 2], [3, 4], [5], hit_count=5)

  assert http.collect_pages(fetch_page) == (5, [1, 2, 3, 4, 5])


def test_collect_pages_stops_at_max_results():
  fetch_page = _serve_pages([1, 2], [3, 4], [5], hit_count=5)

  assert http.collect_pages(fetch_page, max_results=3) == (5, [1, 2, 3])


def test_collect_pages_stops_when_the_cursor_repeats():
  def fetch_page(cursor):
    return [1], 10, cursor

  assert http.collect_pages(fetch_page) == (10, [1])
