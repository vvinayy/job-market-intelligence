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
| `jobmarket.bat` | The only launcher: scrape → API → wait for `/health` → dashboard. `--skip-scrape` / `--scrape-only` select a stage. |
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
.\jobmarket.bat                   # scrape, then API + dashboard
.\jobmarket.bat --skip-scrape     # both services, no re-scrape
.\jobmarket.bat --scrape-only     # scrape + snapshot only (Task Scheduler runs this)

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

**The skills pattern generalizes to any normalized fact.** `role_categories`, `departments`,
`industry_types`, and the education taxonomy (`education_degrees`, `education_specializations`,
`education_degree_specializations`) all use the same get-or-create resolution as skills
(`job_database.py::_resolve_reference_ids` / `_resolve_degree_ids`) — an unseen value auto-registers
rather than being dropped or rejected. Cities are the deliberate exception: `cleaning.py::CITY_ALIASES`
is a curated map, not auto-registering, because `cities.state` is `NOT NULL` and can't be derived from
a bare city fragment — a genuinely new city needs a human to add it (`resolve_locations()`).

**Multi-valued normalized facts are array-of-ids, one row per posting.** Same shape as
`posting_skills(job_id, skill_ids INT[])`: `posting_qualification_degrees`,
`posting_qualification_specializations`. Never one row per value — that was tried and reverted once
already. A *nested* relationship (a degree's own specializations) that can't fit a flat array without
losing the pairing gets its own small dictionary table for the pairing itself
(`education_degree_specializations`), so the per-posting table still stays one row per posting.

**`cleaned_postings` duplicates every normalized array onto itself.** `city_ids`, `skill_ids`,
`preferred_skill_ids`, `accepted_degree_ids`, `accepted_degree_specialization_ids` all live directly
on `cleaned_postings` *and* in their own normalized table — production computation reads
`cleaned_postings` directly, so a fact that only exists in the normalized table is invisible to it.
Add new columns to the UPSERT in both places or they go stale on a repeat sighting.

**Array-column semantics aren't visible from the type.** `skill_ids` on a posting means "all of these
together" (AND); `accepted_degree_ids` means "any one of these satisfies the requirement" (OR) — same
`INT[]` type, opposite real-world meaning. The `accepted_` prefix exists specifically to make the OR
reading unambiguous from the column name alone; keep using it (or an equivalent) for new OR-semantics
arrays rather than adding a second qualifier column, unless the semantics are genuinely more complex
than a single AND/OR (a real dual-degree AND requirement has been checked for live on Naukri and not
found — the array is OR by construction, not by omission).

**Derived text is recomputed, not stored.** `responsibilities_text` / `requirements_text`
were columns until they were shown to be a pure function of `description` — byte-identical
on all 447 rows — so they duplicated ~24% of the table to hold nothing new.
`api/routers/postings.py` now calls `cleaning.py::split_description_sections()` per posting
at read time (the detail endpoint handles one row, so this is cheap) and the API contract is
unchanged. This is the API's one import from outside `api/`. Don't re-add them as columns;
apply the same test to any new derived column — if it recomputes exactly, don't store it.

**Repeat sightings bloat the table.** Every re-sighting is an `UPDATE`, and Postgres writes
a new row version each time — 1,211 updates across 447 rows left the table at more than
double its live size. `VACUUM FULL ANALYZE cleaned_postings` reclaimed 3416 kB → 1424 kB
(58%), which also cleared the accumulated dropped-column overhead. Autovacuum reclaims
*reusable* space but never returns it to the OS; plan a periodic rewrite. At production
scale use `pg_repack` instead — `VACUUM FULL` takes an exclusive lock for the whole rewrite,
which is instant at 447 rows and very much not at millions.

**A closed, stable vocabulary is `TEXT` + `CHECK`, not a reference table.** `working_type`,
`employment_type`, `contract_type` are small fixed sets (3 / 2 / 5 values) that Python already
collapses to one canonical spelling per category (`cleaning.py::EMPLOYMENT_TYPES`/
`CONTRACT_TYPES` map every spelling Naukri uses — `"full-time"`, `"Full Time"` — to a single
output). A `CHECK (col IN (...))` constraint on `cleaned_postings` enforces that against bugs,
with no JOIN and no get-or-create resolution step. Reach for a reference table only when the
vocabulary is open-ended (skills, degrees) or Naukri-tag-driven and genuinely growing
(`role_category`/`department`/`industry_type`) — not for a column that will only ever hold a
handful of known values.

**Every query is parameterised.** Values go to psycopg2 separately; use `WhereBuilder`.
Anything that can't be parameterised (sort columns) is validated against an allowlist —
see `SORTABLE` in `api/routers/postings.py`. Never interpolate caller input into SQL.

**The sample is not the market.** Postings come from a fixed set of searches and cities.
That caveat is surfaced in the API description, the dashboard (`dc.sampling_note()`), and
the README. Preserve it in any new user-facing summary.

**Scraper etiquette.** One visible browser reused for the entire run (Naukri blocks
headless), randomized 3–6s throttle between detail pages. Don't switch to headless, don't
parallelize, don't remove the throttle. `discover_job_urls()` also rejects any href that isn't
a `naukri.com` URL before queuing it — a defensive guard against a stray off-site link being
scraped with Naukri's own selectors, which would silently produce garbage output (wrong title,
wrong company) rather than a caught error.

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
- `cleaned_postings` column order is deliberate (identity → education → skills → the rest,
  matching how a JD is actually read) and `schema.sql` matches the live table exactly.
  Postgres cannot reorder columns in place, so that order was applied by rebuilding the
  table. `ALTER TABLE ADD COLUMN` appends to the end and will drift from `schema.sql`'s
  order — cosmetic only, since every query names its columns, but rebuild if it matters.
  A rebuild must restore, by name: 4 CHECK constraints, 3 outgoing FKs, the PK, the UNIQUE
  on `fingerprint`, 6 indexes, the `job_id` sequence, and the **5 incoming FKs** from
  `posting_cities` / `posting_skills` / `posting_qualifications` /
  `posting_qualification_degrees` / `posting_qualification_specializations`. Do it in one
  transaction (Postgres DDL is transactional) and compare a per-column `md5(string_agg(...))`
  before committing — a row count alone won't catch a mis-ordered copy. Watch for a
  duplicate index: an inline `UNIQUE` in `CREATE TABLE` already creates one, so also
  issuing `CREATE UNIQUE INDEX` for the same column leaves two.
- `snapshot_daily_skills()` must run after the day's scrape. A day not snapshotted is
  gone permanently; `cleaned_postings` only ever shows the present. It is now called from
  `naukri_collector.py::_snapshot_today()` at the end of **every** scrape, not only from
  `jobmarket.bat` — a scrape launched any other way used to skip it silently. The
  SQL function recalculates on a same-day re-run, so calling it once per search URL is
  safe. Failure prints a loud `[SNAPSHOT FAILED ...]` line: three days (Aug 18–20 2026)
  were lost because the function still referenced `posting_skills.skill` after that table
  moved to `skill_ids INT[]`, and the error went into a log nobody reads. If you change
  `posting_skills`, re-apply `trends_setup.sql` — the function is not updated automatically.
- The skill blocklist filters trend *views* only, not the snapshot, so history survives a
  later change of mind.
- `jobmarket.bat` hardcodes `C:\Users\Acer\Webscraping_Extraction` and the psql path. It
  replaced the old `run_daily_scrape.bat` + `start_demo.bat` pair — a Windows Task
  Scheduler entry pointing at either old name needs updating to
  `jobmarket.bat --scrape-only`.
- **Batch files must be ASCII with CRLF line endings.** `cmd.exe` mis-parses LF-only `.bat`
  files — `goto` targets and multi-line `if (...)` blocks break, and it silently eats
  leading characters off lines rather than erroring. Most editors and the write tooling
  here default to LF, so check after editing: `[System.IO.File]::WriteAllText($p, ($t
  -replace "\`r\`n","\`n" -replace "\`n","\`r\`n"), [Text.Encoding]::ASCII)`. Keep em
  dashes and smart quotes out of `.bat` files for the same reason.
- `.claude/settings.local.json` is gitignored and already allowlists the common psql and
  health-check commands.
- `naukri_role` is a recruiter-picked dropdown value on Naukri's own posting form, not
  something derived from the JD text — verified noisy against real data: the same title
  ("SQL Developer", "Machine Learning Engineer") gets different `naukri_role` values on
  different postings, sometimes "Other", and occasionally something the JD flatly
  contradicts (a "Forward Deployed Engineer" tagged "Pre Sales Engineer"). Useful as a raw
  signal, not as ground truth for anything.
- `resolve_locations()` strips a parenthetical locality suffix before matching
  (`"Hyderabad( Raidurgam )"` → `Hyderabad`) — a real fragment seen in production, not a
  hypothetical. Fragments like `"pan india"` or a bare state name (`"Telangana"`) are left
  in `unmapped_locations` on purpose; they aren't cities and shouldn't become one.

## Known open items

- **`description_hash` cross-company anomaly.** One group of 5 postings (2 companies, 2
  unrelated titles — Cisco "Software Engineer" and Fractal Analytics "Full stack Developer")
  shares identical, unrelated description text (an "IoT Intern / Drone Technology / Indore"
  JD that matches neither posting). The other 9 duplicate-description groups in the DB are
  legitimate same-company reposts, so this looks like a rare glitch rather than a systemic
  bug, but root cause is unconfirmed — Naukri 403s non-browser requests (confirmed via
  WebFetch), so verifying live needs an actual headed Playwright run against one of the
  affected URLs, which hasn't been done yet.
