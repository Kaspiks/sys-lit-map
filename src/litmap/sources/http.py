"""HTTP for the source adapters: retries on temporary failures, readable errors, cursor paging."""
import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 5
ERROR_BODY_CHARS = 300  # enough for the API's own explanation of what went wrong


def _is_temporary(error):
  """Retry dropped connections, rate limits (429) and server errors, never bad queries."""

  if isinstance(error, requests.HTTPError):
    status = error.response.status_code

    return status == 429 or status >= 500

  return isinstance(error, (requests.ConnectionError, requests.Timeout))


@retry(
  retry=retry_if_exception(_is_temporary),
  wait=wait_exponential(min=1, max=30),
  stop=stop_after_attempt(MAX_ATTEMPTS),
  reraise=True,
)
def request(method, url, params):
  """Send a request. POST puts params in the body: hundreds of PMIDs would overflow a URL."""
  payload = {"params": params} if method == "GET" else {"data": params}
  response = requests.request(method, url, timeout=TIMEOUT_SECONDS, **payload)

  if not response.ok:
    # the API's own explanation, but not response.url, which would print the API key
    message = f"{response.status_code} from {url}: {response.text[:ERROR_BODY_CHARS]}"
    raise requests.HTTPError(message, response=response)

  return response


def get_json(url, params):
  return request("GET", url, params).json()


def collect_pages(fetch_page, max_results=None):
  """Follow cursor paging until the results run out or max_results is reached.

  fetch_page(cursor) returns (records on that page, total hit count, next cursor);
  the first cursor is "*". Returns (total hit count, records).
  """

  cursor, records = "*", []

  while True:
    page, hit_count, next_cursor = fetch_page(cursor)
    records.extend(page)

    no_more_pages = not page or next_cursor in (None, cursor)
    have_enough = len(records) >= (max_results or hit_count)

    if no_more_pages or have_enough:
      return hit_count, records[:max_results]

    cursor = next_cursor
