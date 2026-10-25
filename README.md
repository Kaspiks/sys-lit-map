# sys_lit_map

A tool for systematic literature mapping: search, deduplication, screening, extraction and PRISMA outputs.

## Pipeline

Design goal: **high recall**. Each stage removes only clear negatives; anything doubtful moves on.
The full plan is in [docs/architecture.md](docs/architecture.md).

| # | Stage | Module | Output | Status |
|---|---|---|---|---|
| 1 | Search: Europe PMC, PubMed, OpenAlex (no year or language limits in the queries) | `search.py`, `sources/` | `data/raw/<run>/`, `logs/search.jsonl` | done |
| 2 | Deduplication: DOI → PMID → title → fuzzy title, never across different DOIs; then a check of doubtful years | `dedup.py`, `years.py` | `data/interim/records.parquet`, `logs/dedup.jsonl` | done |
| 3 | Screening: rule pre-filter → ranker → human decisions | `rules.py`, `rank.py`, `screening.py`, `ui/` | `data/interim/prefiltered.parquet`, `logs/screening.jsonl` | done |
| 4 | Citation chasing via OpenAlex, new records back through stage 2 | `chase.py` | `data/raw/chase/<round>/`, `logs/chase.jsonl` | done |
| 5 | Extraction: intrinsic and extrinsic factors, filled in from the full texts | `extract.py` | `data/processed/extraction.xlsx`, `.csv` | done; form is a draft |
| 6 | Reporting: PRISMA-ScR flow from the logs, charts, PRISMA-S search report | `report.py` | `outputs/`, `logs/report.jsonl` | done |

The search definition, scope limits and validation set live in `config/search.yaml`.

### Running it

In IPython inside the container:

```python
from litmap import search, dedup, rules

search.run_search()    # new run folder in data/raw/, checks the validation set
dedup.deduplicate()    # latest run -> data/interim/records.parquet (record ids stay stable on reruns)
rules.apply_rules()    # language/year rules -> data/interim/prefiltered.parquet
```

Each stage reads the previous stage's output file, so any stage can be rerun alone.
Every step appends to `logs/<stage>.jsonl` with SHA-256 hashes of its input and output files.

To try one database on its own, for example while tuning the search terms:

```python
from litmap.config import load_config
from litmap.sources import pubmed

cfg = load_config()
pubmed.make_query(cfg)                                  # the query exactly as it will be sent
hit_count, raw = pubmed.search(cfg, max_results=20)
pubmed.to_frame(raw)                                    # the common record table
```

`europepmc` and `openalex` have the same three functions.

### Screening

Start the screening interface in the container, then open http://localhost:8000 on the Mac:

```bash
docker exec -it litmap python -m litmap.ui
```

- Keys: `I` include, `M` maybe, `E` exclude (then `1`-`6` for the reason), `U` undo, `B` bookmark, `Esc` cancel.
  On a phone, swipe left to exclude and right to include.
- Every decision and undo is appended to `logs/screening.jsonl`, and the screen is rebuilt from that
  log on every action, so closing the browser loses nothing.
- Records come in a fixed random order (the seed set that will train the ranker), with the
  validation studies hidden inside it. Model scores are never shown.
- Your initials, the seed, the seed set size, the stopping rule and the exclusion reasons are in
  `config/screening.yaml`. Logs store the reason codes, so labels can be reworded safely.
- Under each record, the concept check shows which search.yaml terms it contains, block by block.
  For a block with none, it suggests the exclusion reason that fits (`when_missing` in
  `config/screening.yaml`); you still decide.
- `B` bookmarks the record you're reading, for studies you want to come back to even if they don't
  fit the review. A bookmark isn't a decision, so decide as usual; it never counts in screening, the
  ranker or the PRISMA flow. The **Bookmarks** page (`/bookmarks`) lists them with your decision and
  a note for why each is interesting. Bookmarks are logged in `logs/bookmarks.jsonl` and kept when
  you start a new search.
- The sidebar has the other pages, all read from the logs:
  - **Record trail** (`/trail`): everything the logs say about one record (which search found it,
    how it was merged, which rules ran, every screening event), whether the fingerprinted files are
    unchanged, plus tabs for all rule exclusions, all deduplication joins and the raw event logs.
    `python -m litmap.trail <record id>` prints the same trail in the terminal.
  - **Citation registry** (`/registry`): every deduplicated record and where it stands.
  - **PRISMA flow** (`/prisma`): the flow diagram and charts, and a button that writes the report files.
  - **Ranker** (`/ranker`): the recall curve and every iteration, with a button to replay it.
  - **Second screener** (`/adjudication`): starts blind screening and shows Cohen's kappa.
  - **Config** (`/config`): the config files with their SHA-256, and whether `search.yaml` still
    matches the copy saved with the latest search run. **Start new search & reset screening**
    archives the previous run in `data/archive/<timestamp>-<id>/` (raw data, logs, rankings,
    models, extraction, reports, and a config copy), then runs a fresh database search,
    deduplicates, and applies the current filters. Screening restarts at zero in the seed order
    set by `screening.yaml`; old labels and rankings do not carry over. A failed rebuild restores
    the previous run and keeps failed outputs inside the archive. Progress remains available
    when you return to the page. Keep the UI server running until it finishes.
- A second screener opens `/?screener=XY` with their own initials (or starts from the Second
  screener page). They get a fixed random sample (`second_screener.sample_size`, default 150), in
  random order, without the ranker and without seeing your decisions. Their decisions go to the
  same log under their initials, and agreement on relevance (include or maybe, against exclude) is
  measured with Cohen's kappa.
- The port only accepts connections from this Mac. To screen on a phone on the same Wi-Fi, change
  `127.0.0.1:8000:8000` to `8000:8000` in `docker-compose.yml` and run `docker compose up -d`;
  there is no login, so anyone on that network could then open it.

### Code layout

```
src/litmap/
  common.py        paths, run ids, file hashes, logging, the common record table
  config.py        reading config/search.yaml, screening.yaml and extraction.yaml
  search.py        stage 1: runs every source, saves the run, checks the validation set
  sources/         one adapter per database, all with the same interface
    query.py       concept blocks -> boolean query, query checks, language codes
    http.py        requests with retries, readable errors, cursor paging
    pubmed.py  europepmc.py  openalex.py
  dedup.py         stage 2
  years.py         stage 2: flags theses and repository items dated by a re-registration, corrects them
  rules.py         stage 3, step 1: rule pre-filter
  rank.py          stage 3, step 2: the ranker, its iterations and replay
  screening.py     stage 3: decisions as logged events, queue order, stopping rule, second screener
  agreement.py     your decisions against the blind second screener's: Cohen's kappa
  bookmarks.py     records kept for later, whatever the decision (logs/bookmarks.jsonl)
  trail.py         record trail: one record's history across all logs
  concepts.py      finding the search.yaml concept terms (screen highlighting, ranker features)
  ui/              the interface: Flask app (app.py, views.py) and static/, one HTML page and
                   script per page, sharing layout.js (sidebar and header) and shared.js
  chase.py         stage 4: citation chasing through OpenAlex
  extract.py       stage 5: extraction sheet from config/extraction.yaml, check, export
  report.py        stage 6: PRISMA-ScR counts and flow diagram, charts, PRISMA-S search report
```

## Environment (Docker)

First-time setup, from this folder on the Mac:

```bash
cp .env.example .env
```

Put your email and API keys in `.env`. Then build and start the container:

```bash
docker compose up -d --build
```

### Use it from Docker Desktop

1. **Containers** → `litmap` → **Exec** tab.
2. If the prompt is `#` (plain `sh`), type `bash`.
3. Type `ipython`. pandas, numpy and the project paths are preloaded, and autoreload is on, so edits to `src/litmap/*.py` on the Mac apply immediately.
4. To check packages and API access, type `check`.

### Or from a terminal

```bash
docker exec -it litmap ipython
```

### Notes

- The project folder is mounted at `/workspace`. Everything written to `data/`, `outputs/` and `logs/` ends up on the Mac.
- To run ASReview LAB, start `asreview lab --host 0.0.0.0 --port 5000` inside the container and open http://localhost:5050 on the Mac.
- To run the tests, use `pytest` inside the container.
- The image installs the exact versions in `requirements.lock.txt`, so a rebuild never changes them.
  Cite that file in the methods section for reproducibility. To add a package: add it to
  `requirements.txt`, then run `docker exec litmap pip install -r requirements.txt`, then
  `docker exec litmap pip freeze > requirements.lock.txt`, then `docker compose up -d --build`.

### Ranker

After the 100-record seed set, the screening page orders the remaining records by the latest
ranker iteration, and retrains it after every 25 new decisions (`ranker.batch_size`). It needs
no attention while you screen; scores are logged with each decision but never shown.

- `model` in `config/screening.yaml` switches between `random_forest` and `logistic_regression`;
  `features` picks the feature groups (`words`, `concepts`, `metadata`) for the ablation.
- `python -m litmap.rank` runs one iteration by hand and prints the top records with scores.
- `python -m litmap.rank --replay` rebuilds every logged iteration from the log and checks that it
  gives the same ranking (`--replay 3` for one iteration).
- Each iteration saves its model in `models/`, its ranking in `data/interim/rankings/`, and a
  `rank` line in `logs/screening.jsonl` with the training set, settings, concept terms and hashes.

### Citation chasing

`python -m litmap.chase` runs one round: the records you included (not chased before) are
looked up in OpenAlex, and the works they cite (backward) and the works citing them (forward)
that the pipeline hasn't seen are saved as `data/raw/chase/<round>/`, logged in
`logs/chase.jsonl`. Then run `dedup.deduplicate()` and `rules.apply_rules()`: the new records
join the screening queue, existing record ids stay the same. Repeat after screening them; stop
when a round brings no new includes. Settings: the `chase:` section of `config/search.yaml`.

### Data extraction

The form you fill in while reading each full text is `config/extraction.yaml` (a draft: refine
it with your supervisor). In the container:

```bash
python -m litmap.extract template
```

That writes `data/processed/extraction.xlsx`: one row per study you included or marked maybe,
with its citation and link filled in, dropdowns for the one-answer fields, and the form on a
second sheet. Fill it in in Excel or Numbers. Rerun `template` after more screening: new studies
are added, your entries kept, and the previous file saved as `extraction.previous.xlsx`.
`python -m litmap.extract check` lists every problem; `export` writes the checked rows to
`data/processed/extraction.csv` for the report. Both files are tracked in git.

### Reporting

```bash
python -m litmap.report
```

That writes `outputs/`: `prisma_counts.json` and the PRISMA-ScR flow diagram (`prisma_flow.svg`,
`.png`), the charts in `outputs/figures/` (database overlap, the recall curve, publication years,
and from the extraction export how running experience is reported and which characteristics were
tested as moderators), and `search_report.md`, the search documented following PRISMA-S. Every
number comes from the logs and saved files, and every file is logged with its SHA-256 in
`logs/report.jsonl`. Counts still in progress (awaiting screening, awaiting full-text reading) are
shown as such, so the report can be generated at any point. The PRISMA flow page shows the same
diagram and charts live, and its button runs the same build.

### Debugging in VS Code

Code runs in the container, but you can set breakpoints in VS Code on the Mac and step through it:

1. Start the code under the debugger, in the container's shell:
   ```bash
   debug -m litmap.rank
   ```
   (`debug` is short for `python -m debugpy --listen 0.0.0.0:5678 --wait-for-client`; it works with
   any module, e.g. `debug -m litmap.ui`.) It waits until VS Code attaches.
2. In VS Code: Run and Debug, choose **Attach to container**, press the green play button.
   Breakpoints you set in `src/litmap/` stop there, with variables, the call stack and a debug console.

From IPython: run `debugger()`, attach VS Code the same way, then call your code (e.g. `rank.main()`).
Without VS Code, put `breakpoint()` in the code: it opens IPython's debugger in the terminal.
