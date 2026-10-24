"""Turning the screening state into the JSON the page renders."""
import re

from litmap.common import plain

# PubMed's structured abstracts: "BACKGROUND: ... METHODS: ..."
SECTION_LABEL = re.compile(r"(?:^|(?<=\s))([A-Z][A-Z ,&/-]{2,40}):\s")


def state_view(workspace, concept_terms):
  record_id = workspace.next_record_id()
  record = workspace.pool.loc[record_id] if record_id else None

  return {
    "screener": workspace.settings.screener,
    "progress": workspace.progress(),
    "record": record_view(record_id, record, concept_terms) if record_id else None,
    "recent": [_recent_view(decision, workspace) for decision in workspace.recent()],
    "reasons": [vars(reason) for reason in workspace.settings.exclusion_reasons],
    "legend": concept_terms.legend,
  }


def record_view(record_id, record, concept_terms):
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
    "concept_hits": concept_terms.hit_counts(f"{title} {abstract}"),
    "journal": plain(record["journal"]),
    "year": plain(record["year"]),
    "doi": plain(record["doi"]),
    "pmid": plain(record["pmid"]),
    "pub_types": plain(record["pub_types"]),
    "databases": record["databases"].split("+"),
    "authors": authors.split(", ") if authors else [],
  }


def abstract_sections(abstract):
  """[(label or None, text), ...]: structured abstracts split at their labels."""
  parts = SECTION_LABEL.split(abstract)
  sections = [(None, parts[0].strip())] if parts[0].strip() else []

  for label, text in zip(parts[1::2], parts[2::2]):
    sections.append((label.strip().capitalize(), text.strip()))

  return sections


def _recent_view(decision, workspace):
  reason = workspace.settings.reason(decision["reason"])

  return {
    "record_id": decision["record_id"],
    "title": plain(workspace.pool.loc[decision["record_id"], "title"]),
    "label": decision["label"],
    "reason": reason.label if reason else None,
    "timestamp": decision["timestamp"],
  }
