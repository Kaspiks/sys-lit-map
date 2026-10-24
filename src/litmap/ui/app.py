"""The screening interface's web server: two pages and a small JSON API.

Routes only translate between HTTP and the screening and trail modules, where the logic lives.
"""

from flask import Flask, jsonify, request

from litmap import trail
from litmap.concepts import ConceptTerms
from litmap.config import load_config
from litmap.screening import DecisionError, StaleRecordError, Workspace
from litmap.ui.views import state_view


def create_app(load_workspace=Workspace.load, load_search_config=load_config):
  """The Flask app. Tests pass their own loaders instead of reading the project files."""
  app = Flask(__name__)
  app.json.sort_keys = False  # keep concept blocks in search.yaml order

  def current_state(workspace):
    concept_terms = ConceptTerms(load_search_config()["concepts"])

    return state_view(workspace, concept_terms)

  @app.get("/")
  def screening_page():
    return app.send_static_file("screening.html")

  @app.get("/trail")
  def trail_page():
    return app.send_static_file("trail.html")

  @app.get("/api/state")
  def get_state():
    return jsonify(current_state(load_workspace()))

  @app.post("/api/decisions")
  def post_decision():
    body = request.get_json(silent=True) or {}
    workspace = load_workspace()

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
    except DecisionError as error:
      return jsonify(error=str(error)), 400

    return jsonify(current_state(workspace))

  @app.post("/api/undo")
  def post_undo():
    workspace = load_workspace()

    try:
      workspace.undo_last()
    except DecisionError as error:
      return jsonify(error=str(error)), 400

    return jsonify(current_state(workspace))

  @app.get("/api/trail")
  def get_trail():
    record_id = trail.find_record_id(request.args.get("q", ""))

    if record_id is None:
      return jsonify(error="No record with that id, DOI or PMID."), 404

    return jsonify(trail.record_trail(record_id))

  @app.errorhandler(FileNotFoundError)
  def missing_stage_output(error):
    message = f"{error}. Run dedup.deduplicate() and rules.apply_rules() first."

    return jsonify(error=message), 503

  return app
