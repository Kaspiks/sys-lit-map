from litmap.concepts import ConceptTerms, term_pattern

CONCEPTS = {"population": ["runner*"], "exposure": ['"auditory cue"', "metronome*"], "outcome": []}


def test_terms_become_patterns_with_wildcards_and_phrases():
  assert term_pattern("runner*") == r"runner\w*"
  assert term_pattern('"auditory cue"') == r"auditory\s+cue"


def test_terms_are_tagged_with_their_concept_block():
  concept_terms = ConceptTerms(CONCEPTS)

  assert concept_terms.segments("Runners heard an auditory  cue.") == [
    ["Runners", "population"], [" heard an ", None], ["auditory  cue", "exposure"], [".", None],
  ]

  assert concept_terms.hit_counts("runner with metronomes") == {"population": 1, "exposure": 1}
