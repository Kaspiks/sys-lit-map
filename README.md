# sys_lit_map

A tool for systematic literature mapping: search, deduplication, screening, extraction and PRISMA outputs.

## Pipeline

Design goal: **high recall**. Each stage removes only clear negatives; anything doubtful moves on.
The full plan is in [docs/architecture.md](docs/architecture.md).

| # | Stage | Module | Output | Status |
|---|---|---|---|---|
| 1 | Search: Europe PMC, PubMed, OpenAlex (no year or language limits in the queries) | `search.py`, `sources/` | `data/raw/<run>/`, `logs/search.jsonl` | done |
| 2 | Deduplication: DOI → PMID → title → fuzzy title, never across different DOIs | `dedup.py` | `data/interim/records.parquet`, `logs/dedup.jsonl` | done |
| 3 | Screening: rule pre-filter → ranker → human decisions | `rules.py`, `rank.py`, `screening.py`, `ui/` | `data/interim/prefiltered.parquet`, `logs/screening.jsonl` | rules and screening UI done; ranker next |
| 4 | Citation chasing via OpenAlex, new records back through stage 2 | `chase.py` | `data/raw/chase/<round>/`, `logs/chase.jsonl` | plumbing done; OpenAlex calls next |
| 5 | Extraction: intrinsic and extrinsic factors | `extract.py` | `data/processed/extraction.csv` | planned |
| 6 | Reporting: PRISMA-ScR flow from the logs, charts, PRISMA-S search report | `report.py` | `outputs/` | planned |

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

- Keys: `I` include, `M` maybe, `E` exclude (then `1`-`6` for the reason), `U` undo, `Esc` cancel.
  On a phone, swipe left to exclude and right to include.
- Every decision and undo is appended to `logs/screening.jsonl`, and the screen is rebuilt from that
  log on every action, so closing the browser loses nothing.
- Records come in a fixed random order (the seed set that will train the ranker), with the
  validation studies hidden inside it. Model scores are never shown.
- Your initials, the seed, the seed set size, the stopping rule and the exclusion reasons are in
  `config/screening.yaml`. Logs store the reason codes, so labels can be reworded safely.
- The record trail (http://localhost:8000/trail) shows everything the logs say about one record,
  and checks that the files the logs fingerprinted are unchanged.
- The port only accepts connections from this Mac. To screen on a phone on the same Wi-Fi, change
  `127.0.0.1:8000:8000` to `8000:8000` in `docker-compose.yml` and run `docker compose up -d`;
  there is no login, so anyone on that network could then open it.

### Code layout

```
src/litmap/
  common.py        paths, run ids, file hashes, logging, the common record table
  config.py        reading config/search.yaml
  search.py        stage 1: runs every source, saves the run, checks the validation set
  sources/         one adapter per database, all with the same interface
    query.py       concept blocks -> boolean query, query checks, language codes
    http.py        requests with retries, readable errors, cursor paging
    pubmed.py  europepmc.py  openalex.py
  dedup.py         stage 2
  rules.py         stage 3, step 1: rule pre-filter (rank.py: step 2, next)
  screening.py     stage 3: decisions as logged events, queue order, stopping rule
  trail.py         record trail: one record's history across all logs
  concepts.py      finding the search.yaml concept terms (screen highlighting, ranker features)
  ui/              screening interface (Flask app + one HTML page per view)
  chase.py         stage 4: citation chasing through OpenAlex (OpenAlex calls to write)
  extract.py  report.py   stages 5-6 (planned)
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
The OpenAlex calls (`lookup_openalex_id`, `references`, `citations`) are still to write.

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
