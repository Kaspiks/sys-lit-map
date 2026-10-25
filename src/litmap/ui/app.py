"""The screening interface's web server: its pages and a small JSON API.

Routes only translate between HTTP and the pipeline modules, where the logic lives.
"""

import re
from functools import partial

from flask import Flask, Response, abort, g, jsonify, request

import litmap
from litmap import agreement, bookmarks, common, rank, report, search, trail, rules
from litmap.concepts import ConceptTerms

from litmap.config import (
  CONFIG_PATH, EXTRACTION_CONFIG_PATH, SCREENING_CONFIG_PATH, load_config, load_screening_settings,
)

from litmap.screening import DecisionError, StaleRecordError, Workspace, record_statuses
from litmap.ui.views import state_view
from litmap.ui.rebuild import SearchRebuild

PAGES = {
  "/": "screening.html",
  "/bookmarks": "bookmarks.html",
  "/trail": "trail.html",
  "/registry": "registry.html",
  "/prisma": "prisma.html",
  "/ranker": "ranker.html",
  "/adjudication": "adjudication.html",
  "/config": "config.html",
}

CONFIG_FILES = (CONFIG_PATH, SCREENING_CONFIG_PATH, EXTRACTION_CONFIG_PATH)
LOGS = ("search", "dedup", "screening", "bookmarks", "chase", "extraction", "report")
EVENT_LIMIT = 200
INITIALS = re.compile(r"[A-Z]{2,4}")


def create_app(
  load_workspace=Workspace.load,
  load_search_config=load_config,
  load_settings=load_screening_settings,
  retrain=rank.retrain_if_due,
):
  """The Flask app. Tests pass their own loaders and ranker instead of using the project files."""
  app = Flask(__name__)
  app.json.sort_keys = False  # keep concept blocks in search.yaml order
  rebuild = SearchRebuild()
  app.extensions["search_rebuild"] = rebuild

  @app.before_request
  def guard_pipeline_during_rebuild():
    if not request.path.startswith("/api/") or request.path.startswith("/api/config") or request.path == "/api/session":
      return

    while True:
      if rebuild.state()["status"] == "running":
        response = jsonify(
          error="A search rebuild is in progress. Return to Config to check progress.",
          code="search_rebuilding",
        )

        response.headers["Retry-After"] = "1"

        return response, 503

      # Ordinary page requests often run in parallel. Wait for them instead of treating
      # contention as a rebuild; check frequently so a real rebuild does not hold HTTP open.
      if rebuild.access_lock.acquire(timeout=0.05):
        if rebuild.state()["status"] == "running":
          rebuild.access_lock.release()
          continue

        g.pipeline_locked = True

        return

  @app.teardown_request
  def release_pipeline_lock(error):
    if g.pop("pipeline_locked", False):
      rebuild.access_lock.release()

  for path, page in PAGES.items():
    app.add_url_rule(path, endpoint=page, view_func=partial(app.send_static_file, page))

  def retrain_if_due():
    """True if a new ranker iteration ran. A failing ranker never blocks screening."""

    try:
      return retrain()
    except Exception:
      app.logger.exception("The ranker failed; the current order is kept")

      return False

  def current_state(workspace):
    concept_terms = ConceptTerms(load_search_config()["concepts"])
    bookmarked = set(bookmarks.bookmarks(workspace.screener))

    return state_view(workspace, concept_terms, bookmarked)

  def search_sync():
    """How search.yaml compares with the latest search run, or None before the first run."""

    try:
      return search.changes_since_last_run(load_search_config())
    except FileNotFoundError:
      return None

  # Screening

  @app.get("/api/session")
  def get_session():
    settings = load_settings()

    return jsonify(
      question=load_search_config().get("question"),
      screener=settings.screener,
      second_screeners=agreement.second_screeners(settings=settings),
      bookmarks=len(bookmarks.bookmarks(settings.screener)),
      search_sync=search_sync(),
      version=litmap.__version__,
    )

  @app.get("/api/state")
  def get_state():
    return jsonify(current_state(load_workspace(screener=_screener(request.args))))

  @app.post("/api/decisions")
  def post_decision():
    body = request.get_json(silent=True) or {}
    screener = _screener(body)
    workspace = load_workspace(screener=screener)

    try:
      workspace.decide(
        record_id=body.get("record_id"),
        label=body.get("label"),
        reason=body.get("reason"),
        note=body.get("note"),
        seconds=body.get("seconds"),
      )
    except StaleRecordError as error:
      return jsonify(error=str(error), state=current_state(workspace)), 409

    if not workspace.blind and retrain_if_due():
      workspace = load_workspace(screener=screener)

    return jsonify(current_state(workspace))

  @app.post("/api/undo")
  def post_undo():
    workspace = load_workspace(screener=_screener(request.get_json(silent=True) or {}))
    workspace.undo_last()

    return jsonify(current_state(workspace))

  # Bookmarks: kept for later, whatever the decision

  @app.get("/api/bookmarks")
  def get_bookmarks():
    workspace = load_workspace(screener=_screener(request.args))
    statuses = {row["record_id"]: row for row in record_statuses(workspace)}
    marks = bookmarks.bookmarks(workspace.screener)

    return jsonify([
      {**mark, **{key: statuses.get(record_id, {}).get(key) for key in ("status", "detail")}}
      for record_id, mark in reversed(marks.items())  # newest first; no status: not in this search run
    ])

  @app.post("/api/bookmarks")
  def post_bookmark():
    body = request.get_json(silent=True) or {}
    screener = _screener(body) or load_settings().screener
    record_id = body.get("record_id")

    if not body.get("bookmarked", True):
      if record_id in bookmarks.bookmarks(screener):
        bookmarks.remove(record_id, screener)

      return jsonify(record_id=record_id, bookmarked=False)

    try:
      event = bookmarks.add(record_id, screener, note=body.get("note"))
    except KeyError:
      return jsonify(error="No record with that id."), 404

    return jsonify(record_id=record_id, bookmarked=True, note=event["note"])

  # Record trail and citation registry

  @app.get("/api/trail")
  def get_trail():
    record_id = trail.find_record_id(request.args.get("q", ""))

    if record_id is None:
      return jsonify(error="No record with that id, DOI or PMID."), 404

    return jsonify(trail.record_trail(record_id))

  @app.get("/api/registry")
  def get_registry():
    return jsonify(record_statuses(load_workspace()))

  @app.get("/api/merges")
  def get_merges():
    return jsonify(trail.merges())

  @app.get("/api/events")
  def get_events():
    log = request.args.get("log", "screening")

    if log not in LOGS:
      return jsonify(error=f"Unknown log {log!r}; choose from {', '.join(LOGS)}."), 400

    limit = request.args.get("limit", EVENT_LIMIT, type=int)
    events = common.read_log(log)
    numbered = [{"line": line, "event": event} for line, event in enumerate(events, start=1)]

    return jsonify(log=log, total=len(events), events=numbered[::-1][:limit])

  # Report and ranker

  @app.get("/api/prisma")
  def get_prisma():
    return jsonify(report.prisma_counts())

  @app.get("/api/figures/<name>.svg")
  def get_figure(name):
    if name != report.FLOW_DIAGRAM and name not in report.FIGURES:
      abort(404)

    svg = report.figure_svg(name)

    if svg is None:
      return jsonify(error="No data for this figure yet."), 404

    return Response(svg, mimetype="image/svg+xml")

  @app.post("/api/report")
  def post_report():
    return jsonify(files=[common.relative(path) for path in report.build()])

  @app.get("/api/ranker")
  def get_ranker():
    workspace = load_workspace()

    return jsonify(
      progress=workspace.progress(),
      settings=workspace.settings.ranker,
      iterations=rank.iterations(),
    )

  @app.post("/api/ranker/replay/<int:iteration>")
  def post_replay(iteration):
    try:
      reproduced = rank.replay(iteration)
    except ValueError as error:
      return jsonify(iteration=iteration, error=str(error)), 409

    return jsonify(iteration=iteration, reproduced=reproduced)

  # Second screener and config

  @app.get("/api/agreement")
  def get_agreement():
    settings = load_settings()
    screeners = agreement.second_screeners(settings=settings)
    second = _screener(request.args) or next(iter(screeners), None)
    result = agreement.agreement(second, settings=settings) if second else None

    if result:
      titles = load_workspace().pool["title"]

      for disagreement in result["disagreements"]:
        disagreement["title"] = common.plain(titles.get(disagreement["record_id"]))

    return jsonify(second_screeners=screeners, sample_size=settings.second_sample_size, agreement=result)

  @app.get("/api/config")
  def get_config():
    files = [
      {"file": common.relative(path), "text": path.read_text(), "sha256": common.sha256(path)}
      for path in CONFIG_FILES
    ]

    return jsonify(files=files, search_sync=search_sync(), rebuild=rebuild.state())

  @app.post("/api/config/reload")
  def post_reload_config():
    """Read the current config files; pipeline loaders already read from disk on each call."""

    return get_config()

  @app.post("/api/config/rebuild")
  def post_rebuild_config():
    """Start search → deduplication → filters without holding a long HTTP request open."""

    return jsonify(rebuild.start(load_search_config())), 202

  @app.get("/api/config/rebuild")
  def get_rebuild_config():
    return jsonify(rebuild.state())

  @app.errorhandler(DecisionError)
  def invalid_request(error):
    return jsonify(error=str(error)), 400

  @app.errorhandler(FileNotFoundError)
  def missing_stage_output(error):
    return jsonify(error=f"{error}. Run the earlier pipeline stages first (see the README)."), 503

  return app


def _screener(values):
  """Screener initials from a query string or request body: None means screening.yaml's screener."""
  initials = str(values.get("screener") or "").strip().upper()

  if initials and not INITIALS.fullmatch(initials):
    raise DecisionError("Screener initials are 2 to 4 letters, like KM.")

  return initials or None
