"""Finding the search.yaml concept terms in text.

The screening page uses this to highlight terms, the ranker to count them as features.
"""

import re


def term_pattern(term):
  """'runner*' -> r'runner\\w*' and '"auditory cue"' -> r'auditory\\s+cue'."""
  words = term.strip('"').split()

  return r"\s+".join(re.escape(word).replace(r"\*", r"\w*") for word in words)


class ConceptTerms:
  """Splits text into pieces tagged with the concept block (population, ...) they belong to."""

  def __init__(self, concepts):
    self.blocks = [block for block, terms in concepts.items() if terms]

    self.legend = [
      {"block": block, "terms": [term.strip('"') for term in concepts[block]]}
      for block in self.blocks
    ]

    groups = [
      f"(?P<{block}>{'|'.join(term_pattern(term) for term in concepts[block])})"
      for block in self.blocks
    ]

    self.pattern = re.compile(r"\b(?:" + "|".join(groups) + r")\b", re.IGNORECASE)

  def segments(self, text):
    """[[text, block or None], ...] covering the whole text in order."""
    pieces, position = [], 0

    for match in self.pattern.finditer(text):
      if match.start() > position:
        pieces.append([text[position:match.start()], None])

      pieces.append([match.group(), match.lastgroup])
      position = match.end()

    if position < len(text):
      pieces.append([text[position:], None])

    return pieces

  def hit_counts(self, text):
    """How often each concept block's terms occur in the text."""
    counts = dict.fromkeys(self.blocks, 0)

    for match in self.pattern.finditer(text):
      counts[match.lastgroup] += 1

    return counts

  def term_hits(self, text):
    """Which terms of each block occur, and how often: {"population": {"runners": 2}, ...}."""
    hits = {block: {} for block in self.blocks}

    for match in self.pattern.finditer(text):
      term = " ".join(match.group().lower().split())
      hits[match.lastgroup][term] = hits[match.lastgroup].get(term, 0) + 1

    return hits
