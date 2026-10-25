# Literature Mapping Tool — Architecture Plan

Oct 24, 2026 · @Kaspars Minajevs

A config-driven pipeline that finds every study relevant to the scoping review, logs each step, and leaves every inclusion decision to a person.

## Goal and scope

The design goal is **high recall**: miss no relevant study, accept extra reading. Every stage removes only clear negatives; anything doubtful moves on.

In practice: the search queries carry no year or language limits. Those limits are applied after deduplication as named, logged rules (screening step 1), and a record whose language or year is unknown is kept.

**Review it serves (pilot):** In studies of healthy adults running to music or a metronome, which participant characteristics have been examined as moderators of cadence entrainment or synchronisation, and how is running experience reported?

| Criterion | Include | Exclude |
| --- | --- | --- |
| Population | Healthy adults, any training level (novice to elite) | Clinical populations, children, animals |
| Activity | Running, treadmill or overground | Walking-only, cycling, other sports |
| Stimulus | Music or metronome used as pacing | No auditory stimulus |
| Outcome | Cadence, step frequency, synchronisation or entrainment | Only performance, affect or physiology |
| Design | Any empirical design | Reviews (used for citation chasing only), opinion pieces |
| Language | English | Other languages (stated limitation) |

**Out of scope for the coursework:** non-English databases, AI-assisted extraction (future work), subscription databases (Scopus, Web of Science, SPORTDiscus).

## Pipeline at a glance

&#91;embedded content: pipeline · 9 stages, 2 loops, 1 shared log\]

Records move left to right along the top row and back along the bottom. The two loops are the ranker retraining on each screening decision, and citation chasing feeding new records from included studies back through deduplication.

## Stages

Six stages, each a separate module that reads the previous stage's output file and writes its own. Any stage can be rerun alone.

| # | Stage | Module | Input | Output | Key design decisions |
| --- | --- | --- | --- | --- | --- |
| 1 | Search | `search.py`, one adapter per source in `sources/` | `config/search.yaml` | `data/raw/<run>/`: `config.yaml`, `<source>.json`, `records.csv` | One adapter per source translates the concept blocks into that API's syntax; no year or language limits in the queries; every query string, date, hit count and file hash logged; raw responses stored unchanged; validation set checked on every run |
| 2 | Deduplication | `dedup.py`, `years.py` | `data/raw/<run>/records.csv` | `data/interim/records.parquet` | Normalise DOIs (lowercase, strip prefixes); link same DOI → same PMID → same normalised title → fuzzy title (token_sort_ratio ≥ 95, years ≤ 1 apart, same numbers in both titles); title and fuzzy matches never join records with different DOIs, so they only match records without a DOI; keep the richest metadata; stable record ids across reruns; log every merge; then dissertations and repository items dated the search year, or more than a year after related works (same author, similar title), are flagged, and the latter get the related works' year, logged with its source |
| 3 | Screening | `rules.py`, `rank.py`, `screening.py`, `ui/` | deduplicated records | decisions log | Rules drop clear negatives; ranker orders the rest; a person decides every record; exclusion reason required |
| 4 | Citation chasing | `chase.py` | studies included in stage 3 | new candidate records | Backward (references) and forward (citing works) via OpenAlex; repeat until a round adds no new includes; new records re-enter deduplication, which keeps existing record ids |
| 5 | Extraction | `extract.py` | included studies | `data/processed/extraction.csv` | Fixed schema: intrinsic factors (experience, musical background, sex, age, preferred cadence) and extrinsic factors (stimulus, tempo, instruction, setting) |
| 6 | Reporting | `report.py` | logs + extraction | `outputs/` | PRISMA-ScR flow counts computed from logs, never typed by hand; summary charts; search report following PRISMA-S |

**Sources:** OpenAlex, Europe PMC and PubMed (open; raw results can be shared). Semantic Scholar optional. Crossref only for DOI checks, since its search is relevance-ranked, not boolean.

## Screening design

Machines rank and suggest; a person makes every inclusion and exclusion decision.

1. **Rule pre-filter.** Removes only unambiguous negatives: records outside the configured languages or years (unknown values are kept), animal studies, no running, no auditory stimulus, clinical-only populations. Each rule is a named, versioned function; every record it removes is logged with the rule's name and version, and every rule's flag is kept as a feature for the ranker.
2. **Ranker (recall-oriented screening prioritisation).** Random forest on title, abstract and metadata features (TF-IDF, rule flags; embedding similarity optional), compared against logistic regression. Seeded with 50–100 hand-labelled records, including the known key studies. Retrained after each batch of decisions (certainty-based active learning).
3. **Title and abstract screening.** Three options: include, exclude, maybe. Maybe goes to full-text check. Model scores are hidden during screening to avoid anchoring.
4. **Stopping rule.** Stop after 100 consecutive irrelevant records **and** all known key studies found. Both reported.
5. **Full-text eligibility.** Includes and maybes only; open-access full text where available, otherwise via library access.
6. **Rejected-pile audit.** A random 5–10% sample of records removed at each stage is checked by hand; the estimated false-negative rate is reported.

**Screening interface:** keyboard shortcuts and swipe (left to exclude, right to include; vertical swipes stay free for scrolling); key terms highlighted (running, music, metronome, cadence, synchronisation); a concept check per record (which search.yaml blocks it matches, with the fitting exclusion reason suggested for a block with no terms); one-tap exclusion reasons; undo; an event feed of the latest decisions and undos; a blind second-screener mode for a fixed random sample of 150 records, with Cohen's kappa computed automatically.

**The other pages read the same logs** and change nothing except where noted: the record trail (one record's history across every log, with file fingerprints checked), the citation registry (every record's status), the PRISMA flow (diagram and charts, plus a button that writes the report files), the ranker (recall curve, iterations and replay), the second screener's agreement, and the config files with their SHA-256 and whether search.yaml still matches the latest search run.

## Data, logging and reproducibility

Every result can be traced back to a logged query or a logged human decision, and replayed from stored data.

| Folder | Holds |
| --- | --- |
| `config/` | `search.yaml` (concept blocks, sources, scope limits, validation set, citation chasing), `screening.yaml` (screener, seed, seed set size, stopping rule, exclusion reason codes, ranker settings, second-screener sample size), `extraction.yaml` (the data-charting form) |
| `data/raw/` | One folder per search run: untouched API responses (one file per source), the config used, all records stacked |
| `data/interim/` | Deduplicated records, rule-flagged records, rankings per iteration |
| `data/processed/` | Labelled set, extraction table |
| `models/` | Trained ranker per iteration |
| `logs/` | `search.jsonl`, `dedup.jsonl`, `screening.jsonl`, `chase.jsonl`, `extraction.jsonl`, `report.jsonl` (append-only) |
| `outputs/` | PRISMA-ScR flow, charts, search report |

**One screening log line per event**, for example: `{"iteration": 4, "event": "decision", "record_id": "openalex:W123", "rank": 3, "score": 0.81, "label": "exclude", "reason": "no auditory stimulus", "screener": "KM", "timestamp": "…"}`

**Pinned for reproducibility:**

- Random seed for the ranker and all sampling
- Exact package versions (`requirements.lock.txt`) and the Docker image
- Embedding model version, if used
- SHA-256 hash of each input file, recorded in the log

`python -m litmap.rank --replay` rebuilds every logged ranker iteration from its logged training set, settings, concept terms and seed, and checks that every ranking is identical; it refuses when the screening pool's SHA-256 no longer matches the logged one.

## Evaluation plan

The evaluation tests completeness first, then effort saved, then reproducibility.

| Question | Measure | How |
| --- | --- | --- |
| Does the search find what it should? | Recall on a validation set | Known key studies (Van Dyck 2015, 2021; Bood 2013; Nijs 2020; Buhmann 2018; others) all retrieved? |
| Is deduplication accurate? | Precision and recall of merges | Hand-checked random sample of 200 records |
| How much does citation chasing add? | New includes per round | Count of includes found only by chasing |
| How much effort does ranking save? | WSS@95 and records screened to reach 100% recall | Simulation: replay screening in ranked order; compare with random order and with ASReview |
| Which ranker features help? | Recall and WSS per feature set | Ablation: words only → + rule flags → + embeddings |
| Are relevant studies being lost? | Estimated false-negative rate | Rejected-pile audit at each stage |
| Is screening consistent? | Cohen's kappa | Blind second screener on a random sample |
| Is it reproducible? | Identical rankings on replay | `replay.py` from logs and stored data |

## Build order

Build one stage at a time and test it before moving on; full draft by Dec 15, 2026, submission Jan 7, 2027.

1. **Week of 26 Oct:** final tool name; protocol and inclusion criteria written; `search.yaml` finalised; validation set of known studies listed.
2. **2 Nov – 15 Nov:** search adapters for OpenAlex, Europe PMC, PubMed; logging; raw storage. Done when all known studies are retrieved.
3. **16 Nov – 22 Nov:** deduplication, checked on a hand sample.
4. **23 Nov – 6 Dec:** rules, screening interface, ranker; screen to the stopping rule.
5. **7 Dec – 13 Dec:** citation chasing, full-text check, extraction table, PRISMA flow and charts.
6. **14 Dec – 20 Dec:** evaluation runs (simulation, ablation, audit, replay).
7. **21 Dec – 7 Jan:** write sections 4–6 and revise. Sections 1–3 are written alongside weeks 1–4.

**Cut order if behind:** embedding features → ablation → ranker (fall back to keyword ordering) → second citation-chasing round. Never cut: search validation, the rejected-pile audit, logging.

## Open decisions

| Decision | Options | Who decides | By when |
| --- | --- | --- | --- |
| Tool name | Rename away from "Litmap" (clashes with the commercial Litmaps) | You, with supervisor | Before the protocol is written |
| Semantic Scholar as a fourth source | Include (better CS/engineering coverage, API key needed) or leave out (simpler, three sources) | You | Before the search runs |
| Second screener | Who screens the 100–200 record sample for kappa; a classmate, Danso's group, or your supervisor | You, ask early | Mid-November |
| AI and ML use in coursework | Faculty rules on using a classifier in a graded project and on AI tools during development | Faculty / supervisor | Now |
| Citation style | IEEE (CS default) or APA (matches the scoping review) | Supervisor | Before writing |
| Report length | Confirm the expected page count and which sections are graded | Supervisor | Before writing |
| Inclusion edge cases | Walking-to-running studies, cycling, virtual or simulated running | You, logged as protocol decisions | Before screening |
| Stopping rule threshold | Number of consecutive irrelevant records before stopping (e.g. 100 or 5% of the pool) | You, fixed in advance | Before screening |
| Languages | English only (as in the criteria above) or also Russian (`search.yaml` currently allows `en, ru`) | You | Before screening |
| Year limits | `search.yaml` has 1990–2025; `year_to: 2025` excludes 36 records dated only 2026. Keep, move to 2026, or drop | You | Before screening |
