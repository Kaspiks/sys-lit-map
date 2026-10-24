"""Stage 4: citation chasing.

Backward (references) and forward (citing works) via OpenAlex for the studies included in
screening; repeat until a round adds no new includes. New records are flattened with
sources.openalex.to_frame and go back through dedup.deduplicate, which keeps existing
record ids so screening decisions stay attached.

OpenAlex raw records already carry `referenced_works`; citing works come from the `cites:` filter.
"""
