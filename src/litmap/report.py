"""Stage 6: reporting. Every number comes from the logs and saved data, never typed by hand.

  prisma_counts()   the PRISMA-ScR flow numbers, stage by stage, including what is still waiting
  flow_diagram()    the PRISMA-ScR flow diagram (graphviz)
  FIGURES           summary charts: database overlap, screening progress (the recall curve),
                    publication years, and from the extraction sheet how running experience is
                    reported and which characteristics were tested as moderators
  search_report()   the search documented following PRISMA-S, as Markdown

build() writes them all to outputs/ and logs every file with its SHA-256 in logs/report.jsonl.

Run: python -m litmap.report
"""

import json
import platform
from collections import Counter
from io import BytesIO

import graphviz
import pandas as pd
from matplotlib.figure import Figure

import litmap
from litmap import common, screening, search
from litmap.config import load_config, load_screening_settings
from litmap.sources import europepmc, openalex, pubmed

DATABASE_NAMES = {"pubmed": "PubMed", "europepmc": "Europe PMC", "openalex": "OpenAlex"}

INTERFACES = {
  "pubmed": f"NCBI E-utilities ({pubmed.ESEARCH_URL}, then efetch)",
  "europepmc": f"Europe PMC REST API ({europepmc.URL})",
  "openalex": f"OpenAlex API ({openalex.URL})",
}

FLOW_DIAGRAM = "prisma_flow"
FULL_TEXT_NOT_RETRIEVED = "no_full_text"  # extraction.yaml's reason for a report that couldn't be read
BAR_COLOUR, HIGHLIGHT_COLOUR, GUIDE_COLOUR = "#7b8794", "#2f6f4f", "#b0b7bf"
FIGURE_SIZE = (7, 3.4)


def prisma_counts(workspace=None):
  """The PRISMA-ScR flow numbers. Raises FileNotFoundError while a stage hasn't run yet."""
  dedup_summary = _latest("dedup", "summary")
  rule_summary = _latest("screening", "rule_summary")
  searches = _search_events(dedup_summary["search_run"])
  chase_rounds = [event for event in common.read_log("chase") if event.get("event") == "round"]
  exports = [event for event in common.read_log("extraction") if event.get("event") == "export"]
  full_text = exports[-1] if exports else {"included": 0, "excluded": {}}

  workspace = workspace or screening.Workspace.load()
  labels = Counter(decision["label"] for decision in workspace.decisions.values())

  screening_reasons = Counter(
    decision["reason"] for decision in workspace.decisions.values() if decision["label"] == "exclude"
  )

  not_retrieved = full_text["excluded"].get(FULL_TEXT_NOT_RETRIEVED, 0)

  excluded_full_text = {
    reason: count for reason, count in full_text["excluded"].items() if reason != FULL_TEXT_NOT_RETRIEVED
  }

  sought = labels["include"] + labels["maybe"]
  assessed = full_text["included"] + sum(excluded_full_text.values())
  databases = {event["source"]: event["records_saved"] for event in searches}
  chased = sum(len(event["new_works"]) for event in chase_rounds)

  return {
    "search_run": dedup_summary["search_run"],
    "databases": databases,
    "citation_chasing": chased,
    "chase_rounds": len(chase_rounds),
    "identified_matches_dedup": sum(databases.values()) + chased == dedup_summary["records_in"],
    "duplicates_removed": dedup_summary["duplicates_removed"],
    "excluded_by_rules": rule_summary["excluded"],
    "screened": len(workspace.decisions),
    "awaiting_screening": len(workspace.pool) - len(workspace.decisions),
    "excluded_at_screening": dict(screening_reasons),
    "sought_for_retrieval": sought,
    "not_retrieved": not_retrieved,
    "assessed_for_eligibility": assessed,
    "awaiting_full_text": sought - assessed - not_retrieved,
    "excluded_at_full_text": excluded_full_text,
    "included": full_text["included"],
  }


def flow_diagram(counts=None):
  """The PRISMA-ScR flow diagram as a graphviz graph (.pipe(format="svg") renders it).

  The main column runs from identification to inclusion; the box beside each step holds what
  left the flow there.
  """

  counts = counts or prisma_counts()
  settings = load_screening_settings()
  diagram = graphviz.Digraph("prisma_scr")
  diagram.attr(rankdir="TB", nodesep="0.6", ranksep="0.35", splines="polyline", fontname="Helvetica")
  diagram.attr("node", shape="box", style="rounded", fontname="Helvetica", fontsize="10", margin="0.15,0.08")
  diagram.attr("edge", arrowsize="0.7")
  main = {"group": "main", "width": "3.4"}  # the main column: one straight line of equally wide boxes

  databases = [f"{DATABASE_NAMES.get(name, name)} (n = {n})" for name, n in counts["databases"].items()]
  identified = sum(counts["databases"].values())
  rules = counts["excluded_by_rules"]
  screening_reasons = counts["excluded_at_screening"]
  full_text_reasons = counts["excluded_at_full_text"]

  _box(diagram, "databases", f"Records identified from databases (n = {identified})", databases, **main)

  _box(diagram, "chasing", f"Records identified by citation chasing (n = {counts['citation_chasing']})",
       [f"{counts['chase_rounds']} round(s)"])

  diagram.node("joined", "", shape="point", width="0", group="main")  # where both sources meet

  _box(diagram, "removed", "Records removed before screening", [
    f"Duplicates (n = {counts['duplicates_removed']})",
    f"Excluded by rules (n = {sum(rules.values())})",
    *[f"  {_readable(rule)} (n = {n})" for rule, n in rules.items()],
  ])

  _box(diagram, "screened", f"Records screened (n = {counts['screened']})",
       [f"Awaiting screening (n = {counts['awaiting_screening']})"], **main)

  _box(diagram, "excluded", f"Records excluded (n = {sum(screening_reasons.values())})",
       _reason_lines(screening_reasons, settings))

  _box(diagram, "sought", f"Reports sought for retrieval (n = {counts['sought_for_retrieval']})", **main)
  _box(diagram, "not_retrieved", f"Reports not retrieved (n = {counts['not_retrieved']})")

  _box(diagram, "assessed", f"Reports assessed for eligibility (n = {counts['assessed_for_eligibility']})",
       [f"Awaiting full-text reading (n = {counts['awaiting_full_text']})"], **main)

  _box(diagram, "excluded_full_text", f"Reports excluded (n = {sum(full_text_reasons.values())})",
       [f"{_readable(reason)} (n = {n})" for reason, n in full_text_reasons.items()])

  _box(diagram, "included", f"Studies included in the review (n = {counts['included']})", **main)

  for source in ("databases", "chasing"):
    diagram.edge(source, "joined", arrowhead="none")

  for tail, head in [("joined", "screened"), ("screened", "sought"), ("sought", "assessed"),
                     ("assessed", "included")]:
    diagram.edge(tail, head)

  for step, beside in [("databases", "chasing"), ("joined", "removed"), ("screened", "excluded"),
                       ("sought", "not_retrieved"), ("assessed", "excluded_full_text")]:
    with diagram.subgraph() as row:
      row.attr(rank="same")
      row.edge(step, beside, style="invis" if beside == "chasing" else "solid")

  return diagram


def databases_figure():
  """How many records each combination of databases found: the overlap between them."""
  records = pd.read_parquet(common.INTERIM_DIR / "records.parquet")

  combinations = records["databases"].map(
    lambda names: " + ".join(DATABASE_NAMES.get(name, name) for name in names.split("+"))
  ).value_counts().sort_values()

  figure = Figure(figsize=FIGURE_SIZE)  # not pyplot: the UI draws figures in several threads
  axes = figure.subplots()
  axes.barh(combinations.index, combinations.values, color=BAR_COLOUR)
  axes.bar_label(axes.containers[0], padding=3, fontsize=8)
  axes.set_xlabel("Records after deduplication")
  axes.set_title("Which databases found each record", loc="left", fontsize=11)

  return _tidy(figure)


def screening_progress_figure():
  """Relevant records found as screening goes on, against what a random order would find."""
  decisions = list(screening.Workspace.load().decisions.values())

  if not decisions:
    return None

  relevant = pd.Series([decision["label"] in screening.RELEVANT_LABELS for decision in decisions]).cumsum()
  screened = range(1, len(decisions) + 1)
  settings = load_screening_settings()

  figure = Figure(figsize=FIGURE_SIZE)
  axes = figure.subplots()
  axes.plot(screened, relevant, color=HIGHLIGHT_COLOUR, linewidth=2, label="Your screening order")

  axes.plot([0, len(decisions)], [0, relevant.iloc[-1]], color=GUIDE_COLOUR, linestyle="--",
            label="A random order, on average")

  if len(decisions) > settings.seed_set_size:
    axes.axvline(settings.seed_set_size, color=GUIDE_COLOUR, linestyle=":", label="End of the seed set")

  axes.set_xlabel("Records screened")
  axes.set_ylabel("Relevant (include or maybe)")
  axes.set_title("Relevant records found while screening", loc="left", fontsize=11)
  axes.legend(frameon=False, fontsize=8)

  return _tidy(figure)


def years_figure():
  """Publication years of the screening pool, with the records you found relevant."""
  workspace = screening.Workspace.load()
  years = workspace.pool["year"].dropna().astype(int)

  relevant_ids = [record_id for record_id, decision in workspace.decisions.items()
                  if decision["label"] in screening.RELEVANT_LABELS]

  relevant_years = workspace.pool.loc[relevant_ids, "year"].dropna().astype(int)

  figure = Figure(figsize=FIGURE_SIZE)
  axes = figure.subplots()
  all_counts = years.value_counts().sort_index()
  axes.bar(all_counts.index, all_counts.values, color=BAR_COLOUR, label="Screening pool")

  if not relevant_years.empty:
    relevant_counts = relevant_years.value_counts().sort_index()
    axes.bar(relevant_counts.index, relevant_counts.values, color=HIGHLIGHT_COLOUR, label="Relevant so far")

  axes.set_xlabel("Publication year")
  axes.set_ylabel("Records")
  axes.set_title("Publication years", loc="left", fontsize=11)
  axes.legend(frameon=False, fontsize=8)

  return _tidy(figure)


def extraction_figure():
  """The review question's answers so far: how experience is reported, which moderators were tested."""
  path = common.PROCESSED_DIR / "extraction.csv"
  included = pd.read_csv(path) if path.exists() else pd.DataFrame()
  included = included[included["full_text"] == "include"] if not included.empty else included

  if included.empty:
    return None

  figure = Figure(figsize=(FIGURE_SIZE[0] * 1.4, FIGURE_SIZE[1]))
  experience, moderators = figure.subplots(1, 2)
  _answer_bars(experience, included["experience_measures"], "How running experience is reported")
  _answer_bars(moderators, included["moderators_tested"], "Characteristics tested as moderators")

  return _tidy(figure)


FIGURES = {
  "databases": databases_figure,
  "screening_progress": screening_progress_figure,
  "years": years_figure,
  "extraction": extraction_figure,
}


def search_report():
  """The search documented following PRISMA-S, as Markdown."""
  counts = prisma_counts()
  run = counts["search_run"]
  searches = _search_events(run)
  cfg = load_config()
  records = common.read_records(common.RAW_DIR / run / "records.csv")
  validation = search.check_validation(cfg, records)
  dedup_summary = _latest("dedup", "summary")
  rule_summary = _latest("screening", "rule_summary")
  chase_rounds = [event for event in common.read_log("chase") if event.get("event") == "round"]

  lines = [
    "# Search report (PRISMA-S)",
    "",
    f"Generated {common.now()} from the logs and config/search.yaml; nothing in it is typed by hand.",
    "",
    "## Databases searched",
    "",
    "| Database | Interface | Searched | Records |",
    "|---|---|---|---|",
    *[f"| {DATABASE_NAMES.get(event['source'], event['source'])} | {INTERFACES.get(event['source'], '')} "
      f"| {event['searched_at']} | {event['records_saved']} |" for event in searches],
    "",
    f"All databases were searched in one run ({run}), each with the same concept blocks translated",
    "into its own syntax. Limits: "
    + ("none in the queries; language and year limits were applied afterwards as rules (below)."
       if not any(event.get("filters_in_query") for event in searches) else "applied in the queries."),
    "",
    "## Search strategies, exactly as sent",
    "",
  ]

  for event in searches:
    lines += [f"### {DATABASE_NAMES.get(event['source'], event['source'])}", "", "```",
              _query_text(event["query"]), "```", ""]

  lines += [
    "## Validation",
    "",
    f"{validation['found']} of {validation['expected']} known key studies retrieved"
    + (": missing " + ", ".join(study.get("label", "?") for study in validation["missing"])
       if validation["missing"] else "."),
    "",
    "## Citation searching",
    "",
  ]

  if chase_rounds:
    lines += [
      "| Round | Seeds | Found backward | Found forward | New records |",
      "|---|---|---|---|---|",
      *[f"| {event['round']} | {len(event['seeds'])} | {event['found'].get('backward', 0)} "
        f"| {event['found'].get('forward', 0)} | {len(event['new_works'])} |" for event in chase_rounds],
    ]
  else:
    lines.append("Not run yet.")

  lines += [
    "",
    "## Deduplication",
    "",
    f"{dedup_summary['records_in']} records in, {dedup_summary['duplicates_removed']} duplicates removed, "
    f"{dedup_summary['records_out']} records out. Linked by DOI, then PMID, then normalised title, then "
    f"fuzzy title (token sort ratio >= {dedup_summary['fuzzy_threshold']}, years at most "
    f"{dedup_summary['max_year_gap']} apart, same numbers in both titles); title matches never join "
    "records with different DOIs. Joins per rule: "
    + ", ".join(f"{rule} {n}" for rule, n in dedup_summary["joins"].items()) + ".",
    "",
    "## Rules applied before screening",
    "",
    *[f"- {_readable(rule)} (version {version}): {rule_summary['excluded'].get(rule, 0)} records excluded"
      for rule, version in rule_summary["rules"].items()],
    "",
    "## Reproducibility",
    "",
    f"- litmap {litmap.__version__}, Python {platform.python_version()}, exact package versions in "
    f"requirements.lock.txt (SHA-256 {common.sha256(common.ROOT / 'requirements.lock.txt')[:16]}…)",
    *[f"- {event['source']} raw response: {event.get('raw_file', 'not fingerprinted')} "
      f"(SHA-256 {event['raw_sha256'][:16]}…)" if event.get("raw_sha256")
      else f"- {event['source']} raw response: logged before file hashing" for event in searches],
    f"- Deduplicated records: {dedup_summary['output_file']} "
    f"(SHA-256 {dedup_summary['output_sha256'][:16]}…)",
    "",
  ]

  return "\n".join(lines)


def build():
  """Write every report file to outputs/ and log them. Returns the paths written."""
  outputs = common.OUTPUTS_DIR
  (outputs / "figures").mkdir(parents=True, exist_ok=True)
  counts = prisma_counts()
  written = []

  def save(path, content):
    if isinstance(content, bytes):
      path.write_bytes(content)
    else:
      path.write_text(content)

    written.append(path)

  save(outputs / "prisma_counts.json", json.dumps(counts, indent=2))
  diagram = flow_diagram(counts)
  save(outputs / "prisma_flow.svg", diagram.pipe(format="svg"))
  save(outputs / "prisma_flow.png", diagram.pipe(format="png"))

  for name, make_figure in FIGURES.items():
    figure = make_figure()

    if figure is not None:
      path = outputs / "figures" / f"{name}.png"
      figure.savefig(path, dpi=200, bbox_inches="tight")
      written.append(path)

  save(outputs / "search_report.md", search_report())

  common.append_log("report", {
    "event": "report",
    "timestamp": common.now(),
    "files": [{"file": common.relative(path), "sha256": common.sha256(path)} for path in written],
  })

  for path in written:
    print(common.relative(path))

  return written


def figure_svg(name):
  """One chart or the flow diagram as SVG bytes (the UI shows them live), or None when there's no data."""

  if name == FLOW_DIAGRAM:
    return flow_diagram().pipe(format="svg")

  figure = FIGURES[name]()

  if figure is None:
    return None

  buffer = BytesIO()
  figure.savefig(buffer, format="svg", bbox_inches="tight")

  return buffer.getvalue()


def _latest(log, event_name):
  matching = [event for event in common.read_log(log) if event.get("event") == event_name]

  if not matching:
    raise FileNotFoundError(f"No {event_name} in logs/{log}.jsonl yet: run that stage first")

  return matching[-1]


def _search_events(run):
  return [
    event for event in common.read_log("search")
    if event.get("run") == run and event.get("event", "search") == "search"
  ]


def _box(diagram, name, heading, lines=(), **attributes):
  """A box with a heading and lines below it, all left-aligned (graphviz's \\l line ends)."""
  label = "\\l".join([heading, *lines]) + "\\l"
  diagram.node(name, label, **attributes)


def _reason_lines(reasons, settings):
  """'not_running: 3' as 'Not running (walking, cycling, other sports) (n = 3)'."""
  lines = []

  for code, count in reasons.items():
    reason = settings.reason(code)
    lines.append(f"{reason.label if reason else _readable(code)} (n = {count})")

  return lines


def _readable(code):
  return code.replace("_", " ").capitalize()


def _query_text(query):
  if isinstance(query, str):
    return query

  if isinstance(query, list):
    term, params = query

    return "\n".join([term, *[f"{name}={value}" for name, value in (params or {}).items()]])

  return "\n".join(f"{name}={value}" for name, value in query.items())


def _answer_bars(axes, answers, title):
  counts = answers.dropna().str.split(";").explode().str.strip().value_counts().sort_values()
  axes.barh([_readable(answer) for answer in counts.index], counts.values, color=BAR_COLOUR)
  axes.set_title(title, loc="left", fontsize=10)
  axes.set_xlabel("Included studies")


def _tidy(figure):
  for axes in figure.axes:
    axes.spines[["top", "right"]].set_visible(False)
    axes.tick_params(labelsize=8)

  figure.tight_layout()

  return figure


if __name__ == "__main__":
  build()
