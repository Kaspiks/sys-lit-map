"""Source adapters: one module per database, all with the same interface.

  make_query(cfg, filters=False)                the query exactly as sent, for the log
  search(cfg, max_results=None, filters=False)  -> (total hit count, raw records)
  to_frame(raw_records)                         -> the common record table (common.RECORD_COLUMNS)

Adding a database means adding a module with these three functions and listing it below.
"""

from litmap.sources import europepmc, openalex, pubmed

SOURCES = {"pubmed": pubmed, "europepmc": europepmc, "openalex": openalex}
