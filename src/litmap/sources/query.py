"""Turning the concept blocks of search.yaml into boolean queries. Shared by every adapter."""
import re

# config uses ISO 639-1 codes ("en"); each database wants its own form
LANGUAGE_NAMES = {"en": "english", "ru": "russian", "de": "german", "fr": "french", "es": "spanish"}

# MEDLINE 3-letter codes: Europe PMC's LANG field and PubMed's XML both use them
MEDLINE_LANGUAGES = {"en": "eng", "ru": "rus", "de": "ger", "fr": "fre", "es": "spa"}
ISO_LANGUAGES = {medline: iso for iso, medline in MEDLINE_LANGUAGES.items()}


def build_query(concepts):
  """OR the terms inside each concept block, AND the blocks together.

  {"population": ["runner*", "marathon*"], "outcome": ["pace"]}
  -> "(runner* OR marathon*) AND (pace)"
  """

  blocks = [" OR ".join(terms) for terms in concepts.values() if terms]

  return " AND ".join(f"({block})" for block in blocks)


def check_query(query):
  """Raise on mistakes that Europe PMC and OpenAlex silently answer with misleading counts."""

  if query.count("(") != query.count(")") or query.count('"') % 2:
    raise ValueError(f"Unbalanced brackets or quotes in query: {query}")

  quoted_phrases = re.findall(r'"([^"]*)"', query)

  if any("*" in phrase for phrase in quoted_phrases):
    raise ValueError(f"Wildcard inside a quoted phrase, list the variants instead: {query}")
