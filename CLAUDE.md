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
a posting came from is `cleaned_postings.source`. The sample leans Naukri —
631 postings against 197 as of 8 Sep 2026 — but **not because of the searches**:
there are 5 Naukri searches against 7 hirist, and per day of collection hirist
actually yields more (28 against 19). The gap is time in service. Naukri has
been collected since 6 August, hirist only since 2 September, so 34 days
against 7. Treat per-board comparisons accordingly, and expect the imbalance
to narrow on its own.

A separate daily job re-visits stored URLs to find postings Naukri has expired,
which the scraper itself can never see (explained below). It covers Naukri
only; hirist publishes no expiry signal at all, which is settled rather than
pending (explained below).

Single-developer project, Windows-first: PowerShell, `.bat` launchers, Windows
Task Scheduler. Python 3.13, PostgreSQL 18 at
`C:\Program Files\PostgreSQL\18\bin\psql.exe`.

**The two scheduled tasks, as actually registered** (read from Task Scheduler
2026-09-16, both Enabled):

| Task | Runs | At |
| --- | --- | --- |
| `JobMarket` | `jobmarket.bat` — **no argument** | 11:00 daily |
| `JobMarket Liveness Check` | `jobmarket.bat --check-only` | 17:00 daily |

The first one does **not** pass `--scrape-only`, though this file and
`jobmarket.bat`'s own header comment both used to say it did. With no argument
the batch file falls through to `:scrape` and then keeps going, so every 11:00
run also starts uvicorn and Streamlit in two `cmd /k` windows that stay open
until someone closes them. The scrape itself is unaffected. Either pass
`--scrape-only` in the task or accept the two windows — but do not trust a
comment about it over `schtasks /query`.

**There is no virtualenv.** `python` resolves to the Microsoft Store build
(3.13.14) and every dependency lives in its user site-packages. A Store update
to 3.14 would move that path and take every package with it, and the 11:00 run
would fail at `import playwright` — which, because the collector never reaches
`_snapshot_today()`, silently costs that day's snapshot as well.

## Getting it running

```powershell
pip install -r requirements.txt; pip install -r api/requirements.txt
playwright install chromium

# one-time database setup, in this order
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d jobmarket -f schema.sql
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -d jobmarket -f trends_setup.sql

.\jobmarket.bat                # scrape, then start API + dashboard
.\jobmarket.bat --skip-scrape  # data is fresh, just start the app
.\jobmarket.bat --scrape-only  # scrape and exit
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
| `naukri_collector.py` | The Naukri scraper. Finds job URLs from a search, then reads each posting. DOM selectors throughout. |
| `hirist_collector.py` | The hirist scraper, and 7 of the 12 daily runs. Needs a browser only to read search results; the detail data comes back as JSON from hirist's own API, which is why its detail throttle is 1–2 s against Naukri's 3–6 s. |
| `skill_taxonomy.py` | Regex vocabulary for finding skills and certifications in description text. |
| `cleaning.py` | Turns raw scraped text into clean values. `clean_record()` is the way in. |
| `job_database.py` | Writes to Postgres. `save_records()` cleans, inserts-or-updates, resolves ids. |
| `liveness.py` | Decides whether a posting URL is still live. Pure functions, no I/O. |
| `liveness_checker.py` | Visits stored URLs daily to find expired postings. Naukri only. |
| `hirist_liveness_probe.py` | Records what a hirist expiry check *would* conclude, into its own table. Writes nothing to `cleaned_postings`. **No longer scheduled** — the rule it tested turned out to be a clock. Kept for a by-hand run around 2026-11-07. |
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

**Run the whole suite, not a subset.** `pytest` collects 234 tests across 11
files. The fast-path command above is four of those files; `test_data_integrity`,
`test_liveness_run`, `test_record_identity`, `test_education_degrees` and
`test_hirist_collector` are in neither that list nor `test_api`. Running only the
advertised subset after the 2026-09-15 split reported a clean 172 while five
tests were broken against columns the split had moved — they were found a day
later, by running everything.

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
the INSERT list, the UPDATE clause *and* the `IS DISTINCT FROM` comparison** in
`UPSERT_SQL`, or it will quietly go stale whenever a posting is seen again.

**`posted_raw` is the one deliberate exception to that rule** — written, but
not compared. It is Naukri's own wording, which ages on its own: an untouched
advert reads "1 day ago", then "2 days ago", then "1 week ago", while
`posted_date` never moves. Comparing it rewrote the whole spine row to record
the calendar turning over. Measured on the first post-split scrape
(2026-09-16): 61 of 213 re-sightings rewrote the spine, and **54 of those 61
carried relative `posted_raw` text** — about 29% of the saving, spent on
nothing. The cost of the exception is that a row skipped for every other
reason keeps the phrase it was first stored with, so `posted_raw` is now
"what the source said when we first read it" rather than "what it says today".
`posted_date` is the fact; this is provenance. Leave a column out of the
comparison only when it moves by itself like this.

**A posting is split across four tables, divided by how often each column is
written.** This replaced a single 45-column `cleaned_postings` on 2026-09-15.

| Table | Holds | Written |
| --- | --- | --- |
| `cleaned_postings` | what the advert *says* — title, company, salary, role, experience | only when the advert actually changes |
| `posting_state` | `url` and the four expiry columns | by the liveness checker; a scrape only clears a stale expiry |
| `posting_content` | `description` | only when the description changes |
| `posting_sightings` | `last_seen_date`, `times_seen`, applicant and company-rating observations | **every scrape** |

`description_foreign_cities` deliberately stays on the spine: it is a 13-byte
flag the list endpoint both displays and filters on (`?description_flagged=`),
and moving it into `posting_content` would put the widest table in the schema on
the list path.

Measured on the live 920 rows before the change: the spine carried 1,194 bytes
over 45 columns at 4.4 rows per 8 kB page, so bumping `last_seen_date` rewrote
a ~2,230-byte tuple. Afterwards the spine is 352 bytes over 26 columns. At 46,000 rows one full
re-sighting went from 14,303 ms and 34 MB of WAL to 206 ms and 8 MB; at 368,000
rows a role-mix scan went from 5,473 ms to 108 ms, because a 419 MB spine no
longer fits in `shared_buffers` and an 83 MB one does.

**The `WHERE` on the upsert is the point, not the split.** A posting re-seen
unchanged now writes *nothing* to the spine. That only became possible once
`last_seen_date` and `times_seen` moved out — while they lived on the row, every
sighting changed it by definition. A skipped row returns nothing from
`RETURNING`, so `save_records()` resolves those job_ids with a follow-up lookup.

**Six array columns were deleted, not moved.** `skill_ids`,
`preferred_skill_ids`, `skill_groups`, `accepted_degree_ids`,
`accepted_degree_specialization_ids` and `city_ids` were byte-identical copies
of `posting_skills` / `posting_qualification_*` / `posting_cities` — verified
0 of 918 rows differing. Their five GIN indexes went with them, having never
been scanned once in the database's life. **The working copies on
`posting_skills` are untouched** and still serve the `?skill=` filter, which
[postings.py](api/routers/postings.py) documents as a measured 14×. This
reverses the older rule that every normalised value was also duplicated onto
`cleaned_postings`; production code now joins for them.

**`posting_state.source` is a deliberate duplicate** of `cleaned_postings.source`.
It never changes after insert, and without it the liveness queue would join back
to the spine purely to filter on it — measured 38 pages against 274. Both copies
are written by the same upsert.

**`skills.category` is a filter, not a label — NULL is the common, correct
state.** About 88% of rows carry no category, and that is the design:
`/analytics/skill-categories` reads `WHERE sk.category IS NOT NULL`, so NULL
is how a broad tag is kept out of the composition chart. The dashboard caption
says so in words — *"broad tags like 'Agile' or 'Communication Skills' are left
out rather than lumped into 'Other'"*. Only add a `SKILL_CATEGORIES` entry for
a specific, placeable technology. Categorising the rest would invert the filter
and make role-fluff ("Java Fullstack", 113 postings) the largest slice of a
chart about technical composition. The existing categories are all one axis —
a layer of the stack — so a value describing *what kind of term this is* does
not belong in the same column; that would need a second column.

**`categorize_skill()` runs once, at registration.** Adding a key to
`SKILL_CATEGORIES` never reaches rows already in `skills`, so every addition
needs a paired backfill migration. Same for `SKILL_ALIASES`: a name added there
later collides with the `_initcap` fallback row already registered for it, so
an alias without a merge migration *widens* the split instead of closing it.
Both happened again on 2026-09-16 — 20 duplicate spellings merged, 39
technologies categorised, chart coverage 68.9% → 71.1%.

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

**hirist expiry cannot be detected, and the rule is dead.** hirist carries no
HTTP signal — HEAD and GET both answer 200 for live and expired postings
alike, and the expired page is drawn in JavaScript. `hasExpired` in the detail
API looked like the answer and is not: measured 2026-09-08, it flips at
**exactly 150 days after `createdTime`** (148d False, 150d True, no exception
in 41 samples spanning 2019 to 2026). It is a clock, so it says nothing about
whether a job closed — and the earlier 40/40 census was measuring the
calendar, which is the one alternative that census was already known not to
exclude.

Nothing corroborates it either: `status`, `active` and `state` all stay `1` on
an expired posting, `hasExpired` is the only one of 78 fields that differs, and
the page's own `redirectedFromExpiredJD` reads false in both states.

**So the 150 days are recorded as a delisting, not a closure.**
`cleaned_postings.expiry_basis` is `observed` or `delisted`, paired to
`is_expired` by a CHECK so neither can be written without the other.
`liveness_checker.mark_delisted_hirist()` sets `expired_on = posted_date +
150` — pure arithmetic, no network, since asking the API would only ask it to
subtract for us — and `/analytics/closures` reads `observed` only. Averaging
the two would report the gap between two boards' retention policies as a
market signal: Naukri's real closures run 0–32 days with a median of 17,
against a hirist constant of 150. `classify_hirist()` still stays out of the
checker, and the nightly probe in `jobmarket.bat` is switched off. **Nothing
is marked delisted yet — the oldest hirist `posted_date` is 10 June 2026, so
the first will be 7 November 2026.**

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
- **`schema.sql` is current again, and the two-step install works.**
  `schema.sql` then `trends_setup.sql` now produces exactly the live schema —
  verified 2026-09-15 by building a database from those two files alone and
  diffing it against `jobmarket`: 0 column differences either way, 25 tables,
  49 indexes, and the real `save_records()` ran against it. The migrations in
  `migrations/` are for bringing an EXISTING database forward and should not be
  run on a fresh one. It had drifted twice — a missing `expiry_basis`, and
  `hirist_liveness_observations` which had only ever existed in a migration —
  so re-run that diff after any schema change.
- **`snapshot_daily_skills()` must run after each day's scrape.** A day not
  snapshotted is gone forever — `cleaned_postings` only shows the present. The
  scraper calls it automatically now. **Editing the function body in
  `trends_setup.sql` changes nothing until you re-apply that file** — the
  function lives in the database, and `CREATE OR REPLACE` only runs when you
  run the script. This was missed during the 2026-09-15 split: the file was
  patched, the database function still referenced the moved
  `cleaned_postings.last_seen_date`, and the next scrape would have lost a day.
  Re-applying is safe — every table is `CREATE TABLE IF NOT EXISTS`, only views
  are dropped, and 14,592 history rows were verified unchanged across it. Three days were lost this way once, and the error went into a
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

- **The 2026-09-15 split is complete.** The migration is applied to
  `jobmarket`, all code is updated, `schema.sql` produces the new shape
  directly, the full suite passes, and the liveness queue,
  `mark_delisted_hirist` and `snapshot_daily_skills()` were each exercised by
  hand afterwards. No posting data changed: every field of all 920 postings was
  compared against the pre-migration dump and matched. The first real exercise
  of the new write path end to end is the next scheduled scrape.

- **Phase 2 of the split is designed but not built.** `companies`, `searches`
  and `naukri_roles` dictionaries, `certifications` and `unmapped_locations` as
  1:N child tables, and retyping `fingerprint` from 64-char hex text to `bytea`
  (65 bytes to 33). Together these take the spine from 339 bytes to roughly
  186. They were left out of phase 1 because they are the modest-bytes tail;
  the measured wins are all in what phase 1 does.

- **`?skills_all=` had no index that could serve it.** Containment over
  `skill_ids || skill_group_ids(skill_groups)` matches neither GIN index, and
  it sequential-scanned even with `enable_seqscan` off. The split migration
  adds `idx_posting_skills_all` over exactly that expression. Unrelated to the
  split; it was simply missing.

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
