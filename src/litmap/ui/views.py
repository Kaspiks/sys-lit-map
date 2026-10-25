"""Turning the screening state into the JSON the page renders."""
import re

from litmap.common import plain

# PubMed's structured abstracts: "BACKGROUND: ... METHODS: ..."
SECTION_LABEL = re.compile(r"(?:^|(?<=\s))([A-Z][A-Z ,&/-]{2,40}):\s")


def state_view(workspace, concept_terms):
  record_id = workspace.next_record_id()
  record = workspace.pool.loc[record_id] if record_id else None
  reasons = workspace.settings.exclusion_reasons

  return {
    "screener": workspace.screener,
    "progress": workspace.progress(),
    "record": record_view(record_id, record, concept_terms, reasons) if record_id else None,
    "queue": workspace.queue_details(record_id)["queue"] if record_id else None,  # never the score
    "feed": feed_view(workspace),
    "reasons": [vars(reason) for reason in reasons],
    "legend": concept_terms.legend,
  }


def record_view(record_id, record, concept_terms, reasons=()):
  title = plain(record["title"]) or "Untitled record"
  abstract = plain(record["abstract"]) or ""
  authors = plain(record["authors"])

  return {
    "record_id": record_id,
    "title": concept_terms.segments(title),
    "sections": [
      {"label": label, "segments": concept_terms.segments(text)}
      for label, text in abstract_sections(abstract)
    ],
    "word_count": len(abstract.split()),
    "concept_check": concept_check(concept_terms.term_hits(f"{title} {abstract}"), reasons),
    "journal": plain(record["journal"]),
    "year": plain(record["year"]),
    "year_reported": plain(record.get("year_reported")),
    "year_flag": plain(record.get("year_flag")),
    "doi": plain(record["doi"]),
    "pmid": plain(record["pmid"]),
    "pub_types": plain(record["pub_types"]),
    "databases": record["databases"].split("+"),
    "n_records": plain(record.get("n_records")),
    "authors": authors.split(", ") if authors else [],
  }


def concept_check(term_hits, reasons):
  """Per concept block: the terms found, and for a block with none, the exclusion reason that fits."""

  suggested = {
    reason.when_missing: {"key": number, "label": reason.label}
    for number, reason in enumerate(reasons, start=1)
    if reason.when_missing
  }

  return [
    {"block": block, "terms": terms, "suggested_reason": None if terms else suggested.get(block)}
    for block, terms in term_hits.items()
  ]


def abstract_sections(abstract):
  """[(label or None, text), ...]: structured abstracts split at their labels."""
  parts = SECTION_LABEL.split(abstract)
  sections = [(None, parts[0].strip())] if parts[0].strip() else []

  for label, text in zip(parts[1::2], parts[2::2]):
    sections.append((label.strip().capitalize(), text.strip()))

  return sections


def feed_view(workspace):
  """The screener's latest decisions and undos; an undo shows the label it took back."""
  decisions = {event["decision_id"]: event for event in workspace.events if event["event"] == "decision"}
  titles = workspace.pool["title"]
  items = []

  for event in workspace.feed():
    decision = decisions.get(event.get("undoes"), event)
    reason = workspace.settings.reason(decision.get("reason"))

    items.append({
      "number": event["number"],
      "event": event["event"],
      "record_id": event["record_id"],
      "title": plain(titles.get(event["record_id"])),
      "label": decision.get("label"),
      "reason": reason.label if reason else None,
      "timestamp": event["timestamp"],
    })

  return items
