# CLAUDE.md

An introduction to this repository, for a person or an AI assistant seeing it
for the first time. It is a summary, not a rulebook — where it disagrees with
the code, the code is right. Deeper design records live in
`docs/superpowers/specs/`.

## What this is

A job market intelligence pipeline over Indian IT postings, scraped from
Naukri.com and hirist.tech. Data flows one way:

```
Naukri.com  ┐
hirist.tech ┴→ Playwright scraper → cleaning (in-process)
             → PostgreSQL → FastAPI → Streamlit dashboard
```

Both boards are scraped by the same 11am task and share every table; which one
a posting came from is `cleaned_postings.source`. The sample is overwhelmingly
Naukri — five searches against three — so treat any per-board comparison
accordingly.

A separate daily job re-visits stored URLs to find postings Naukri has expired,
which the scraper itself can never see (explained below). It covers Naukri
only; hirist expiry is observed but not yet acted on.

Single-developer project, Windows-first: PowerShell, `.bat` launchers, Windows
Task Scheduler. Python 3.13, PostgreSQL 18 at
`C:\Program Files\PostgreSQL\18\bin\psql.exe`.

## Getting it running

```powershell
pip install -r requirements.txt; pip install -r api/requirements.txt
playwright install chromium

# one-time database setup, in this order
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d jobmarket -f schema.sql
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d jobmarket -f trends_setup.sql

.\jobmarket.bat                # scrape, then start API + dashboard
.\jobmarket.bat --skip-scrape  # data is fresh, just start the app
.\jobmarket.bat --scrape-only  # scrape and exit (the 11am scheduled task)
.\jobmarket.bat --check-only   # find expired postings (the 5pm task)

# or run the two services by hand
uvicorn api.main:app --reload   # terminal 1 — docs at /docs
streamlit run Home.py           # terminal 2

pytest                          # everything
pytest tests/test_cleaning.py tests/test_skill_taxonomy.py \
       tests/test_naukri_parsers.py tests/test_liveness.py   # fast, no I/O
```

Configuration is environment variables only, never files: `PGDATABASE`
(default `jobmarket`), `PGUSER` (`postgres`), `PGPASSWORD`, `PGHOST`, `PGPORT`.
The API also accepts `DATABASE_URL`; the dashboard accepts `API_BASE_URL`
(default `http://localhost:8000`).

## Where things are

| Path | Role |
| --- | --- |
| `naukri_collector.py` | The scraper. Finds job URLs from a search, then reads each posting. |
| `skill_taxonomy.py` | Regex vocabulary for finding skills and certifications in description text. |
| `cleaning.py` | Turns raw scraped text into clean values. `clean_record()` is the way in. |
| `job_database.py` | Writes to Postgres. `save_records()` cleans, inserts-or-updates, resolves ids. |
| `liveness.py` | Decides whether a posting URL is still live. Pure functions, no I/O. |
| `liveness_checker.py` | Visits stored URLs daily to find expired postings. Naukri only. |
| `hirist_liveness_probe.py` | Records what a hirist expiry check *would* conclude, into its own table. Writes nothing to `cleaned_postings` — it exists to earn evidence the census could not. |
| `notify.py` | Windows toast notifications, so unattended runs are not silent. |
| `schema.sql` | Every table. Run first. |
| `migrations/` | Dated, idempotent `ALTER`s for a database that already has data — `schema.sql` drops the postings tables, so it cannot bring an existing one forward. |
| `trends_setup.sql` | Daily skill snapshots and the views over them. Run second. |
| `api/` | FastAPI. `main.py` app, `database.py` query helpers, `models.py` response shapes, `routers/` endpoints. |
| `Home.py`, `pages/` | Streamlit dashboard (Skills, Market, Trends, Jobs). |
| `dash_common.py` | The only dashboard file that makes HTTP calls. |
| `jobmarket.bat` | The single launcher. |
| `tests/` | pytest suite. |
| `docs/superpowers/specs/` | Design records for decisions worth keeping. |
| `logs/`, `naukri_jobs.json`, `.auth/` | Generated or secret. Gitignored — never edit or commit. |

## How to verify a change

There is no linter and no formatter. The test suite has three layers, and only
the first runs anywhere:

- **Pure functions** (`test_cleaning`, `test_skill_taxonomy`, `test_naukri_parsers`,
  `test_liveness`) — no database, no network, milliseconds.
- **API contract** (`test_api`) — hits every endpoint in-process against your real
  local Postgres. Read-only; skips itself if the database is unreachable.
- **Dashboard smoke** (`test_dashboard_smoke`) — needs a real `uvicorn` already
  running. Slow, skips itself if nothing is listening.

**A green test run is not enough on its own.** Schema changes, new selectors and
dashboard behaviour need a manual pass too: run a small scrape, hit `/health`,
query Postgres, or open the dashboard.

## The ideas behind the design

**Honest precision — the one that matters most.** Where the source is vague,
store nothing rather than invent something. `"30+ days ago"` becomes a NULL date
with the original text kept alongside; `"100+"` applicants is stored as 100 and
flagged as a floor.

This is easiest to break in a *fallback*. `normalize_working_type()` used to
return `"On-site"` when it found nothing, which put a fabricated value on 372 of
495 rows — Naukri only shows a work-mode badge when a remote arrangement exists,
so its absence means "not stated", never "office". A fallback over a value that
*is* present is fine by contrast: `classify_role()` returning `"Other"` describes
a real title that matched no pattern, and invents nothing.

**No AI in the pipeline.** Extraction is DOM selectors; skills are regex.
Missing labels become NULL. Never add a model call or a guess to fill a gap.

**One direction of dependency.** Dashboard pages → `dash_common` → HTTP → API →
Postgres. Pages never import `psycopg2` and never know a table or column name. A
schema change should be absorbable in `api/routers/` alone.

**The sample is not the market.** Postings come from a fixed set of searches and
cities — mostly Hyderabad. That caveat is surfaced in the API description, the
dashboard, and the README. Keep it in anything new.

**Nothing is stored unprocessed.** The scraper cleans in memory and writes
straight into `cleaned_postings`. Do not add a `raw_postings` table; it existed
once and was deliberately removed.

**Which board a posting came from is a stored column, not a URL match.**
`cleaned_postings.source` is `naukri`, `hirist` or `other`, written by
`cleaning.source_from_url()` on every insert *and* every update. It used to be
re-derived with an ILIKE in each place that needed it, which meant two copies
of the same rule; `snapshot_daily_skills()` and the liveness queue now both
read the column. An unrecognised host records as `other` rather than being
folded into the dominant board.

**Duplicates are caught by fingerprint** — a hash of company + title + location +
experience. Seeing a posting again updates every field, keeps `first_seen_date`,
and bumps `last_seen_date` and `times_seen`. **When you add a column, add it to
both the INSERT list and the UPDATE clause** in `UPSERT_SQL`, or it will quietly
go stale whenever a posting is seen again.

**Open-ended vocabularies get their own table; fixed ones get a CHECK.** Skills,
degrees, departments and industries auto-register when something new appears.
`working_type` and `contract_type` are small fixed sets, so a `CHECK` constraint
is enough. Cities are the deliberate exception — they need a curated entry
because `cities.state` cannot be guessed from a city name, so unrecognised
locations are surfaced rather than invented.

**Three-state booleans are real.** `is_full_time` and `is_expired` are TRUE,
FALSE, *and* NULL for "we don't know". Never test them for truthiness.

**Every query is parameterised.** Use `WhereBuilder`. Anything that cannot be
parameterised — a sort column, a grouping dimension — is checked against an
allowlist. Never put caller input directly into SQL.

**Scraper etiquette.** One visible browser for the whole run (Naukri blocks
headless), and a random 3–6 second pause between pages. Don't go headless, don't
parallelise, don't remove the pause.

**hirist expiry is measured but not trusted yet.** hirist carries no HTTP
signal at all — HEAD and GET both answer 200 for live and expired postings
alike, and the expired page is drawn in JavaScript. The signal is `hasExpired`
in the detail API the collector already calls (and *not* `status` or `active`,
which stayed 1 on 8 of 20 expired postings). What no census can show is a
posting *crossing* between states, so `hirist_liveness_probe.py` records the
verdict nightly without writing it; the first observed flip promotes the rule
into `liveness_checker.py`.

**Expiry needs proof.** The scraper only ever sees postings a search returns, so
it can never revisit an expired one — that is why `liveness_checker.py` reads
URLs from the database instead. Naukri answers an expired posting with a
redirect carrying `expJD=true`, and **only that counts as expired.** A timeout,
a block, or a 404 is "unknown" and writes nothing; otherwise a single bad night
would write off the whole table. The checker uses a plain HEAD request rather
than a browser — measured 40/40 against a browser census — which is why it is
allowed a shorter pause than the scraper.

Fuller versions of several of these live in `docs/superpowers/specs/`, including
why the three skill columns were not merged into one.

## Things that will trip you up

- **`schema.sql` starts by dropping the postings tables.** Running it wipes
  collected data. Never run it just to check something.
- **`snapshot_daily_skills()` must run after each day's scrape.** A day not
  snapshotted is gone forever — `cleaned_postings` only shows the present. The
  scraper calls it automatically now, but if you change `posting_skills` you
  must re-apply `trends_setup.sql`, because the function is not updated
  automatically. Three days were lost this way once, and the error went into a
  log nobody read.
- **A wrong snapshot day is corrected, never edited.** `skill_daily_counts`
  rows stay exactly as observed; adjustments go in `skill_daily_corrections`
  as reasoned rows whose deltas sum, and the trend views read
  `skill_daily_counts_corrected`. `snapshot_coverage` stays on the raw table
  on purpose — a correction does not change which days were recorded. Twenty-
  four corrections exist for 12–17 Aug, where a foreign job description
  inflated C++ ninefold.
- **The skill detector changed on 2026-09-02, and the step it puts in the
  history cannot be corrected away.** `extract_skills()` was rewritten to
  tokenise a description once and look each 1–3 word window up in a dict,
  rather than scanning the whole text once per pattern — 6.6 ms to 0.78 ms,
  and cost is now proportional to description length rather than vocabulary
  size, so adding skills is free. It also finds ten things the old form
  missed, all punctuation cases ("R-programming", "AWS: S3"). Fourteen
  vocabulary entries were added in the same pass, found by using hirist's own
  tags as ground truth. Together: 7.9 to 9.0 skills per description.

  **Why this is not a `skill_daily_corrections` row.** That ledger records an
  observation that was *wrong*. Both of these numbers are right — they were
  measured with different instruments, so there is no delta to write down.
  Nor can the old days be recomputed: `cleaned_postings` only shows the
  present, so nothing knows which postings were live on a past date. And
  `source` does not isolate it the way it isolates a new job board — every
  source shares one detector, so every series steps at once. The fourteen new
  skills recorded 0 every day before this and will appear to surge from
  nothing; on `/trends/movers` they will likely dominate the first run after
  the change. That is the instrument, not the market.
- **Batch files must be ASCII with CRLF line endings.** `cmd.exe` mis-parses
  LF-only `.bat` files and silently eats characters rather than erroring.
  `.gitattributes` enforces this; keep em dashes and smart quotes out.
- **`jobmarket.bat` hardcodes the project path and the psql path.**
- **Postings are updated only when a search surfaces them again.** An expired
  posting never reappears, which is the entire reason the liveness checker
  exists.
- **`naukri_role` is a recruiter-picked dropdown, not derived from the job
  description.** Verified noisy — the same title gets different values, and it
  occasionally contradicts the posting outright. Use it as a raw signal, never
  as ground truth.
- **Repeat sightings bloat the table.** Every update writes a new row version.
  Plan a periodic `VACUUM FULL ANALYZE` (or `pg_repack` at real scale — `VACUUM
  FULL` locks the table for the whole rewrite).

## Style

- Module docstrings explain *why* the file exists and what it deliberately does
  not do.
- Inline comments give the reason, in one or two lines — what would go wrong
  without this, or what was measured. The story of how it was discovered belongs
  in the commit message. A comment longer than the code it explains is a smell.
- Section banners: `# ===...===` in Python, `-- ---...---` in SQL.
- Modern typing: `str | None`, `list[dict]`. No `Optional` or `typing.List`.
- Every endpoint declares a `response_model` and a `summary=`, with the model in
  `api/models.py`.
- Dashboard charts use `dc.PALETTE` / `dc.SCALE` / `**dc.TRANSPARENT`. New API
  calls go through a named wrapper in `dash_common.py`.
- Commit messages: imperative, one line, then a body explaining *why* if the
  reason is not obvious.

## Known open items

- **Naukri sometimes serves a description belonging to a different job.**
  Five rows (644, 645, 815 Cisco; 713, 885 Fractal Analytics) hold an
  IoT/drone/Indore JD that matches neither. Diagnosed, not a scraper bug: the
  scrape logs record the wrong text arriving already mined on six consecutive
  days, and a re-visit on 31 Aug found exactly one JD container per page —
  Cisco corrected at source, Fractal still wrong nineteen days on. Only the
  description and what is mined from it are affected; the fingerprint fields
  are all correct, which is why nothing else could see it. Left in place
  deliberately.

  **A flagged posting is still stored in full, and marked.** The source cannot
  be adjudicated automatically — about half of what the check catches is a
  recruiter naming another office in the body — so nothing is ever rejected or
  hidden. `cleaned_postings.description_foreign_cities` carries the evidence,
  refreshed on every sighting so it clears itself when Naukri corrects the
  description; `?description_flagged=` filters on it, and the Jobs detail
  panel says so in words. See
  `docs/superpowers/specs/2026-08-31-foreign-description-detection.md`.

- **Four location fragments still need a curated `cities` entry**, and only
  two of them are cities: `Srinagar` (2 postings) and `Patiala` (2). Both need
  a state, which is the one thing that cannot be guessed from a city name --
  and `Srinagar` genuinely names two places, in Jammu & Kashmir and in
  Uttarakhand. The rest (`Anywhere in India/Multiple Locations`, `Any
  Location`, `pan india`) are not cities and should stay unmapped.

  The parenthetical pair (`Hyderabad( Raidurgam )`, `Hyderabad( HITEC City )`)
  is resolved: the parser handled them all along, those rows simply predated
  the fix. Seven such stale rows -- three of which carried no city at all --
  were replayed through the alias table by
  `migrations/2026-09-02-backfill-resolvable-locations.sql`.
- **Closure metrics are limited to experience band and role.** Department,
  industry and education look ready but are not: those fields only began being
  collected on 19 Aug, so their "not stated" group is really "collected
  earlier", and earlier postings have had longer to close. Revisit once older
  postings are a small minority.
