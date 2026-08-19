# CLAUDE.md

Guidance for AI assistants working in this repository.

## What this is

A job market intelligence pipeline over Indian IT postings scraped from Naukri.com:

```
Naukri.com → Playwright scraper → Python cleaning (in-process)
           → PostgreSQL → FastAPI → Streamlit dashboard
```

Single-developer project, Windows-first (PowerShell, `.bat` launchers, Windows Task
Scheduler). Python 3.13, PostgreSQL 18 at `C:\Program Files\PostgreSQL\18\bin\psql.exe`.

## Layout

| Path | Role |
| --- | --- |
| `naukri_collector.py` | Playwright scraper: URL discovery + per-posting DOM extraction. CLI entry point. |
| `skill_taxonomy.py` | Regex vocabulary for mining skills/certifications out of description text. |
| `cleaning.py` | The cleaning layer. `clean_record()` is the single entry point. |
| `job_database.py` | Postgres writer: `save_records()` cleans, upserts, resolves skill ids. |
| `schema.sql` | All table shapes. Run first. |
| `trends_setup.sql` | `skill_daily_counts`, `snapshot_daily_skills()`, delta views. Run second. |
| `api/main.py` | FastAPI app; connection pool opened/closed in the lifespan handler. |
| `api/database.py` | Pooled cursor helpers (`fetch_all/one/value`) and `WhereBuilder`. |
| `api/models.py` | Pydantic response models — the API's documented contract. |
| `api/routers/` | `postings`, `reference`, `analytics`, `trends`. |
| `Home.py`, `pages/` | Streamlit multipage dashboard (`1_Skills`, `2_Market`, `3_Trends`, `4_Jobs`). |
| `dash_common.py` | The only dashboard file that talks HTTP. Named wrappers + Plotly palette. |
| `run_daily_scrape.bat` | Scheduled scrape (one line per search URL) → snapshot. |
| `start_demo.bat` | Health-checked launcher: scrape → API → wait for `/health` → dashboard. |
| `logs/`, `naukri_jobs.json`, `.auth/` | Generated/secret. Gitignored, never edit or commit. |
| `tests/` | pytest suite — pure-function units, API contract tests, dashboard smoke tests. |

## Commands

```powershell
pip install -r requirements.txt; pip install -r api/requirements.txt
playwright install chromium

# one-time DB setup, in this order
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d jobmarket -f schema.sql
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d jobmarket -f trends_setup.sql

uvicorn api.main:app --reload     # terminal 1 — docs at /docs
streamlit run Home.py             # terminal 2

python naukri_collector.py "https://www.naukri.com/python-developer-jobs-in-hyderabad" --limit 20
.\start_demo.bat --skip-scrape    # both services, no re-scrape

pytest                             # full suite
pytest tests/test_cleaning.py tests/test_skill_taxonomy.py tests/test_naukri_parsers.py
                                    # pure-function units only — no DB, no server, <1s
```

There is **no linter and no formatter** configured. A `pytest` suite exists in `tests/`
(see below) but doesn't cover everything — schema changes, new selectors, and dashboard
behavior still need a manual pass: run a small scrape, hit `/health`, query Postgres, or
open the dashboard. Don't claim a change is verified without actually doing one of those
in addition to a green test run.

The suite has three layers, and only the first is dependency-free:
- `test_cleaning.py`, `test_skill_taxonomy.py`, `test_naukri_parsers.py` — pure functions,
  no I/O. Always run these after touching `cleaning.py`, `skill_taxonomy.py`, or a parser
  in `naukri_collector.py`.
- `test_api.py` — hits every endpoint through `TestClient(app)` (in-process, no server
  needed) against whatever's in the real local Postgres. Read-only; skips itself if the
  DB isn't reachable. There is no mock/staging DB in this project — see "No raw/staging
  table" below — so this is intentional, not a shortcut.
- `test_dashboard_smoke.py` — `AppTest` on a few dashboard pages, needs a real `uvicorn`
  process already running at `API_BASE_URL` (dashboard pages talk real HTTP, not
  `TestClient`). Skips itself if nothing's listening. Slow (~15s/page) — don't add pages
  to it reflexively, a handful of representative ones is enough to catch a broken import
  or a renamed field.

Config is environment variables only, never files: `PGDATABASE` (default `jobmarket`),
`PGUSER` (`postgres`), `PGPASSWORD`, `PGHOST`, `PGPORT`. The API also accepts
`DATABASE_URL`; the dashboard accepts `API_BASE_URL` (default `http://localhost:8000`).

## Architecture rules that matter

**One direction of dependency.** Dashboard pages → `dash_common` → HTTP → API → Postgres.
Pages never import `psycopg2` and never know a table or column name; `dash_common` knows
only JSON shapes. A schema change should be absorbable in `api/routers/*.py` alone.

**No raw/staging table.** The scraper cleans in-process and writes straight into
`cleaned_postings`. Nothing scraped is ever stored unprocessed. Do not add a
`raw_postings` table — it was deliberately removed (`fb4f8a3`).

**No AI in the pipeline.** Extraction is DOM selectors; skills are regex taxonomy.
Missing labels report the `"not found"` sentinel, which `cleaning.py::_clean()` turns
into SQL `NULL`. Never introduce a model call or a guess to fill a gap.

**Honest precision.** Where the source is imprecise, store nothing rather than invent:
`"30+ days ago"` → `posted_date = NULL` (raw text kept in `posted_raw`), `"100+"`
applicants stored as the floor. Keep this rule when adding fields.

**Dedup is by fingerprint.** SHA-256 of company + title + location + experience
(`cleaning.py::make_fingerprint`). A repeat sighting `ON CONFLICT DO UPDATE`s every
field to its latest value, preserves `first_seen_date`, bumps `last_seen_date` and
`times_seen`. Add new columns to *both* the INSERT list and the SET clause in
`UPSERT_SQL` — omitting one means that field silently goes stale on repeat sightings.
Batches are deduped in Python first, since Postgres can't update the same row twice in
one statement.

**Skills are a dictionary table.** `skills(skill_id, skill_name, category)`, one row per
distinct normalized name. `posting_skills` is one row per *posting* with `skill_ids INT[]`
(GIN-indexed); analytics `unnest()` at query time. `category` is seeded from
`cleaning.py::SKILL_CATEGORIES` only when a skill is first registered and never
overwritten, so `UPDATE skills SET category = ...` is a durable data fix, not a code
change. Array elements can't carry an FK — referential integrity there is an application
guarantee in `job_database.py`.

**Every query is parameterised.** Values go to psycopg2 separately; use `WhereBuilder`.
Anything that can't be parameterised (sort columns) is validated against an allowlist —
see `SORTABLE` in `api/routers/postings.py`. Never interpolate caller input into SQL.

**The sample is not the market.** Postings come from a fixed set of searches and cities.
That caveat is surfaced in the API description, the dashboard (`dc.sampling_note()`), and
the README. Preserve it in any new user-facing summary.

**Scraper etiquette.** One visible browser reused for the entire run (Naukri blocks
headless), randomized 3–6s throttle between detail pages. Don't switch to headless, don't
parallelize, don't remove the throttle.

## Conventions

- Module docstrings explain *why* the file exists and what it deliberately doesn't do.
  Inline comments justify non-obvious decisions (`NULLS LAST`, the `skills_all` count
  guard, `ping` instead of `timeout` in the `.bat`). Match that density — it's the
  house style, not decoration.
- Section banners: `# ===...===` blocks in Python, `-- ---...---` in SQL.
- Modern typing throughout: `str | None`, `list[dict]`. No `Optional`/`typing.List`.
- Every endpoint declares a `response_model` and a `summary=`; add the Pydantic model in
  `api/models.py` rather than returning bare dicts.
- Dashboard charts use `dc.PALETTE` / `dc.SCALE` / `**dc.TRANSPARENT`. New API access
  goes through a named wrapper in `dash_common.py`, not a `requests` call in a page.
- `api_get` caches for 5 minutes and takes params as a **tuple of pairs** (dicts aren't
  hashable for `st.cache_data`); repeated filters are repeated tuple entries.
- Commit messages: imperative, one line, no body, no trailing metadata
  ("Remove full-text description search", "Add skill category mix, crossed with role").

## Gotchas

- `schema.sql` starts with `DROP TABLE` on the postings tables — running it wipes
  collected data. Never run it to "check" something.
- `snapshot_daily_skills()` must run after the day's scrape. A day not snapshotted is
  gone permanently; `cleaned_postings` only ever shows the present.
- The skill blocklist filters trend *views* only, not the snapshot, so history survives a
  later change of mind.
- `run_daily_scrape.bat` and `start_demo.bat` hardcode `C:\Users\Acer\Webscraping_Extraction`
  and the psql path.
- `.claude/settings.local.json` is gitignored and already allowlists the common psql and
  health-check commands.

## Known open items

None currently tracked. Automated tests and scrape observability (`scrape_runs` +
`check_field_health()`) were the two standing gaps; both now exist.
