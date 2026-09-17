# ARCHITECTURE.md

`CLAUDE.md` describes what this repository contains and how to run it. This
file explains **why it is shaped the way it is** — the decisions, what forced
them, what evidence settled each one, and which arguments were considered and
rejected.

It is written to be read cold. Someone picking this project up with no memory
of the conversations behind it should be able to read this file and `CLAUDE.md`
and know not just the current state but *why it is the current state*, so they
do not re-derive a decision that was already settled, or undo one whose reason
is not visible in the code.

Where a claim has a number attached, that number was measured on the date
given, not estimated. Where a figure is arithmetic rather than a measurement,
it says so.

Figures are as of **15 September 2026**: 920 postings, 678 from Naukri since
6 August and 242 from hirist since 2 September.

---

## The shape

```
Naukri.com  ┐
hirist.tech ┴→ Playwright scraper ──→ cleaning, in memory
                                        │
                                        ▼
                        PostgreSQL  ──→ FastAPI ──→ Streamlit
                             ▲
                             └── daily liveness checker (Naukri only)
```

Four processes, one direction. Everything below explains why each boundary sits
where it does.

---

## 1. Honest precision outranks everything else

**Where the source is vague, store nothing rather than invent something.**

Every other decision defers to this one. It is not a style preference; it is
what makes the data worth having. A guessed number is indistinguishable from an
observed one once it is in a column, and every chart built on it inherits the
guess silently.

The rule was learned by breaking it. `normalize_working_type()` once returned
`"On-site"` when it found nothing, putting a fabricated value on **372 of 495
rows**. Naukri only renders a work-mode badge when a remote arrangement exists,
so its absence means *not stated* — never *office*. Today:

```
working_type NULL      714 of 920
working_type 'On-site'   0 of 920
posted_date  NULL      291 of 920
```

Those NULLs are the architecture working. `"30+ days ago"` cannot become a
date, so `posted_date` is NULL and `posted_raw` keeps the original phrase.
`"100+ applicants"` is stored as `100` with a qualifier marking it a floor, so
no reader can mistake it for an exact count.

The distinction that matters: a fallback over a value that *is* present is
fine — `classify_role()` returning `"Other"` describes a real title that matched
no pattern and invents nothing. A fallback over *absence* is a fabrication.

## 2. The dependency runs one way, so a schema change stays local

```
Streamlit pages → dash_common → HTTP → FastAPI → Postgres
```

Pages never import `psycopg2` and never name a table or a column. Verified
rather than assumed: a sweep of `pages/` and `Home.py` on 15 September 2026
found **zero** references to either.

The payoff is concrete. The four-table split below changed the physical schema
underneath every metric in the product, and the dashboard needed **no change at
all** — and 29 of 35 API endpoints needed none either, because they read only
columns that stayed put. A schema change is absorbable inside `api/routers/` by
design, and that design held the first time it was tested seriously.

## 3. A posting is four tables, divided by how often each column is written

Until 15 September 2026 a posting was one 45-column row. The problem was not
width in the abstract — it was that **PostgreSQL rewrites the entire row on any
update**, and the daily scrape updates every posting it re-sees.

Measured on the live 920 rows before the change:

| | |
| --- | --- |
| Columns | 45 |
| Bytes of column data per row | 1,194 |
| Rows per 8 kB page | 4.4 |
| Bytes rewritten to bump `last_seen_date` | ~2,230 |

So the tables are split by **write frequency first**, read frequency second:

| Table | Holds | Written |
| --- | --- | --- |
| `cleaned_postings` | what the advert says — title, company, salary, role, experience | only when the advert actually changes |
| `posting_state` | `url` and the four expiry columns | by the liveness checker; a scrape only clears a stale expiry |
| `posting_content` | `description` | only when the description changes |
| `posting_sightings` | `last_seen_date`, `times_seen`, applicant and company-rating observations | **every scrape** |

`description_foreign_cities` stays on the spine rather than moving with the
description it is derived from. It is 13 bytes, and it is a flag *about* the
posting that `?description_flagged=` filters list queries on — moving it would
make every list request join the widest table in the schema to read one small
array.

The spine drops from 1,194 bytes to **352**, over 26 columns. What that buys,
measured on real rows replicated to scale:

| | 920 rows | 46,000 rows | 368,000 rows |
| --- | --- | --- | --- |
| Analytics scan | 5.3× fewer pages | 5.0× fewer pages | 5.0× fewer pages, **51× faster** |
| One full re-sighting | 13 ms → 3 ms | **14,303 ms → 206 ms** | — |
| WAL for that write | 746 kB → 163 kB | 34 MB → 8 MB | — |
| Total storage | +3% | −1% | — |
| One posting **with** description | 1 page → 2 pages, identical ms | — | — |

**Storage is not the reason.** Splitting relocates bytes and pays tuple
overhead three times instead of once. Anyone proposing a split to save space
has the wrong argument.

### The cache cliff, and where it sits

The read gain only becomes a *time* gain past the point where the table stops
fitting in `shared_buffers`. At 368,000 rows the old spine is 419 MB against a
128 MB `shared_buffers`, so every scan re-fetches it from outside the cache
(`shared read=53665`); the 83 MB core stays resident. Below that line the gain
is real but costs milliseconds.

| | Heap per row | Outgrows a 128 MB cache at |
| --- | ---: | ---: |
| Old 45-column shape | ~1.14 kB | **~110,000 postings** |
| New spine | ~231 B | **~580,000 postings** |

Raising `shared_buffers` moves both edges proportionally. It is one config line
and a restart, and it is the cheapest lever available if this ever bites.

### The `WHERE` is the point, not the split

```sql
ON CONFLICT (fingerprint) DO UPDATE SET ...
WHERE (cleaned_postings.title, ...) IS DISTINCT FROM (EXCLUDED.title, ...)
```

A posting re-seen unchanged — the common case, since an advert rarely changes
after publication — now writes **nothing** to the spine. That clause was
impossible before the split: while `last_seen_date` and `times_seen` lived on
the row, every sighting changed it *by definition*, so the guard could never
fire. The split exists to make the guard possible; the guard collects the
saving.

A skipped row returns nothing from `RETURNING`, so `save_records()` resolves
those `job_id`s with a follow-up lookup on the unique index. **When you add a
column to the spine, add it in three places** — the INSERT list, the UPDATE
clause, and the `IS DISTINCT FROM` comparison. Miss the third and the column
updates when it should not; miss the second and it goes stale silently.

**One column is deliberately left out of the comparison: `posted_raw`.** The
first post-split scrape (2026-09-16) showed the guard firing on 152 of 213
re-sightings — but 61 rows were still rewritten, and **54 of those 61 carried
relative `posted_raw` text** ("1 day ago", "6 days ago"). The advert had not
changed; Naukri's phrase for how old it is had. Because that phrase is a
function of the calendar rather than of the job, comparing it spent ~29% of
the saving rewriting 352-byte rows to record the passage of time.

The tradeoff is explicit: a row skipped for every other reason now keeps the
`posted_raw` it was first stored with. That is acceptable because `posted_date`
— the parsed, absolute fact — is still compared and still refreshed, and
because the raw phrase was always provenance rather than data: it exists so a
reader can see what was parsed, and "what the source said when we first read
it" is a defensible reading of that. It would not be acceptable for any column
that describes the job itself. The test is whether the column changes on its
own without the underlying thing changing; `posted_raw` is currently the only
one that does.

### Why `fillfactor = 70`

An update writes a new row version. If it fits on the **same page**, PostgreSQL
chains it there and skips updating every index — a HOT update. If not, every
index entry is rewritten.

Measured 11 September 2026, sweeping the setting on a wide row:

| fillfactor | HOT updates | Table + indexes |
| ---: | ---: | ---: |
| 100 (default) | 4% | 312 kB |
| 90 | 30% | 296 kB |
| 80 | 54% | 272 kB |
| **70** | **81%** | **232 kB** |
| 60 | 81% | 232 kB |

Reserving space made the table *smaller*: dead row versions from non-HOT
updates cost more than the space held back. 70 is where the curve flattens.

The reserved space is **recycled, not consumed**. Over ten rounds of updates on
60 rows, HOT held at 100% from round five onward while dead rows plateaued at
133 and page count plateaued at 20. Because no index points at the intermediate
versions, PostgreSQL prunes them within the page during ordinary access — no
`VACUUM`, no index work.

The one thing that breaks this is **a transaction left open**: a session holding
an old snapshot stops those versions being pruned, the page genuinely fills, and
updates go off-page again. That is why `jobmarket` has
`idle_in_transaction_session_timeout = '10min'` set at the database level. It
kills only sessions sitting *inside* a transaction doing nothing; it does not
interrupt a running query, and it does not disconnect idle sessions.

Note what the scraper's design contributes here: the scrape happens entirely
**before** any database connection is opened, so ten minutes of Playwright never
sits inside a transaction.

## 4. Why the split happened when it did — and the argument that was wrong

This section exists because the obvious reading of the numbers above points the
other way, and a future reader who re-derives it will reach the wrong
conclusion.

At 22.4 new postings per day — the measured rate, 920 over 41 days — the old
shape would not reach its ~110,000-row cache cliff for **13.3 years**. Half of
it is six years away. The saving at today's size is **0.56 ms** on an analytics
scan and **10 ms per day** on writes.

The first recommendation made from that was *don't split, revisit at ~50,000
postings*. **That recommendation was wrong**, and the reasoning error is worth
recording:

> "When will you need this?" is the right question only for a change that has a
> downside to weigh against. This one does not. Across every dimension
> measured — pages scanned, write volume, WAL, storage, endpoints broken,
> dashboard changes — the split is better or neutral. The detail fetch costs one
> extra page at identical milliseconds. Nothing gets worse.

With no downside on the ledger, the question is not *when will it be needed* but
*is there a reason to keep the worse one*. There was not. And the cost of the
change only ever rises: 920 rows is the smallest migration it will ever be, and
six endpoints the fewest it will ever be.

**The general rule this produced:** apply "wait until you need it" only when
waiting is actually buying something. When a change is strictly better and the
only cost is one-time effort, the cheapest moment to pay it is now.

## 5. What is duplicated on purpose, and what stopped being

Six array columns used to exist on both `cleaned_postings` and the child
tables. That was deliberate once: production code read the spine directly, so
every normalised value was mirrored onto it.

The split reversed that rule, on evidence. The copies were compared row by row
on 15 September 2026:

```
skill_ids           0 of 918 rows differ
preferred_skill_ids 0 of 918 rows differ
skill_groups        0 of 918 rows differ
```

Byte-identical, so they were **deleted rather than moved** — 193 bytes per row,
and five GIN indexes that had never been scanned once in the database's entire
life went with them. Production code now joins for those values.

**One duplication was kept.** `posting_state.source` repeats
`cleaned_postings.source`. It never changes after insert, and without it the
liveness queue would join back to the spine purely to filter on it — measured
38 pages against 274. Both copies are written by the same upsert.

### How a skill choice is represented

A posting requiring "Python **or** Java **or** C#" cannot be expressed as
columns. `posting_skills` holds three fields per posting:

```
skill_ids[]           every one of these is required          (AND)
preferred_skill_ids[] the starred subset, CHECKed as a subset
skill_groups jsonb    one from EACH inner array satisfies it  (OR)
```

358 of 920 postings carry at least one OR-group; the most on a single posting
is ten. `skill_group_ids()` flattens the groups into one id array so a query can
ask "would this posting accept Python?" without unpacking the JSON.

### Why the skill indexes live where they do

The two GIN indexes on `posting_skills` are not incidental — they exist for a
measured reason, and the `?skill=` filter is written the way it is *to let them
work*:

```sql
-- overlap, split and OR'd, so each half can use its own GIN index
ps.skill_ids && (...) OR skill_group_ids(ps.skill_groups) && (...)
```

Concatenating first — `(a || b) && ...` — builds a new array per row and no
index can cover it. Measured 14× slower.

**Containment cannot be split the same way**: "all of these, in a *or* b" is
not "all in a, or all in b". So `?skills_all=` must use the concatenation, and
proven on 15 September 2026 it sequential-scans **even with `enable_seqscan`
off** — no existing index covers that expression. The fix is an index over
exactly it, added by the split migration:

```sql
CREATE INDEX ON posting_skills USING gin ((skill_ids || skill_group_ids(skill_groups)));
```

This gap predated the split and was unrelated to it. It was simply missing.

## 6. Expiry needs its own job, because the scraper can never observe it

The scraper only ever sees postings a search returns. An expired posting is by
definition not returned, so **the scraper can never revisit one**. That single
fact is why `liveness_checker.py` exists: it reads stored URLs out of the
database rather than discovering them.

What counts as proof is deliberately narrow. Naukri answers an expired posting
with a redirect carrying `expJD=true`, and **only that counts as expired** —
verified 495/495. A timeout, a block or a 404 is *unknown* and writes nothing.
Without that rule a single bad night would write off the whole table.

The checker uses a plain HEAD request rather than a browser, measured 40/40
against a browser census, which is why it is allowed a shorter pause than the
scraper.

A scrape reaching a posting **does** clear a stale expiry — a search returning
it is evidence it is live — but deliberately does **not** set
`last_checked_on`. That column means "the checker requested this URL", and
writing it from a scrape conflates coverage with verification. It also made the
evening run skip every posting the morning scrape had re-surfaced.

Today: **273 postings expired, all 273 recorded as `observed`.**

## 7. hirist's expiry is a delisting, not a closure

hirist publishes no HTTP expiry signal at all — HEAD and GET both answer 200
for live and expired postings alike, and the expired page is drawn in
JavaScript after the shell loads.

`hasExpired` in the detail API looked like the answer and is not. Measured
8 September 2026 it flips at **exactly 150 days after `createdTime`** — 148d
false, 150d true, no exception across 41 samples spanning 2019 to 2026. It is a
clock, so it says nothing about whether a job closed. Nothing corroborates it
either: `status`, `active` and `state` all stay `1` on an expired posting, and
`hasExpired` is the only one of 78 fields that differs.

So it is recorded as a **delisting**, never a closure. `expiry_basis` is
`observed` or `delisted`, paired to `is_expired` by a CHECK so neither can be
written without the other, and `/analytics/closures` reads `observed` only.
Averaging the two would report the gap between two boards' retention policies
as a market signal: Naukri's real closures run 0–32 days with a median of 17,
against a hirist constant of 150.

`mark_delisted_hirist()` is pure arithmetic — no network call, because asking
the API would only ask it to subtract for us. No row is marked `delisted` yet:
the oldest hirist `posted_date` is 10 June 2026, so the first falls due on
**7 November 2026**.

## 8. History is snapshots, because the postings table only shows the present

Update a row and yesterday's version is gone. Nothing in `cleaned_postings`
knows which postings were live on a past date, and no amount of recomputation
can recover it.

`snapshot_daily_skills()` runs at the end of every scrape and writes one row
per skill per source per day into `skill_daily_counts`. **A day not snapshotted
is gone forever.** Three days were lost this way once, and the error went into
a log nobody read.

That table is also the only one whose size is a function of the *calendar*
rather than of data volume: 14,592 rows over 29 snapshot days from 920
postings, and nothing ever deletes from it. It is the table that will eventually
need partitioning or a retention policy — not the postings table.

## 9. Two ledgers, because there are two kinds of wrong

A recorded day can be wrong in two different ways, and conflating them destroys
the distinction between a mistake and a measurement.

**`skill_daily_corrections` — the observation was wrong.** Raw counts are never
edited. A reasoned row with a signed delta is appended instead, and the views
sum them. Twenty-four such corrections exist for 12–17 August, where a foreign
job description inflated C++ ninefold.

**`instrument_changes` — the observation was right, the instrument changed.**
On 2 September 2026 `extract_skills()` was rewritten: 6.6 ms to 0.78 ms per
description, and 7.9 to 9.0 skills found. Both numbers are correct — measured
with different instruments, so there is no delta to write down. The fourteen
vocabulary entries added the same day recorded 0 every prior day and will appear
to surge from nothing. That is the instrument, not the market. Five such
changes are recorded, across four dates.

Nor can the old days be recomputed, and `source` does not isolate the step the
way it isolates a new job board — every source shares one detector, so every
series steps at once.

## 10. The read side splits the same way the write side does

Analytics and trends are two separate computations that share almost nothing.

**`/analytics/*` — cross-sectional, computed live, no history.** Nine tables:
`cleaned_postings` and its child tables, `skills`, `skill_blocklist`, `cities`,
`posting_qualifications`, plus `scrape_runs` and `liveness_runs` for
`/scrape-health` alone.

**`/trends/*` — time series, never touches the postings table.** Four base
tables reached through five views: `skill_daily_counts` and
`skill_daily_corrections` feed `skill_daily_counts_by_source` →
`skill_daily_counts_corrected` → the three delta views, with `skill_blocklist`
applied at the top. `instrument_changes` marks the steps.

`skill_blocklist` is the only table in both stacks. `snapshot_daily_skills()` is
the only bridge between them.

Two things worth knowing before working here:

- **`snapshot_coverage` is defined in `trends_setup.sql` and read by nothing.**
  `/trends/coverage` recomputes the same answer directly from
  `skill_daily_counts`. Two routes to one question, one of them maintained.
- **Nine populated dictionary tables produce no analytics number.**
  `departments`, `industry_types`, `role_categories`, the three education
  tables, `states` and the two qualification link tables are read only for
  filtering and listing. Where an endpoint looks like it should use one, it does
  not: `/analytics/roles` groups by the `cleaned_postings.role_family` **text
  column**, and `/analytics/qualifications` by `posting_qualifications.level`.

## 11. Rules the stored data obeys

These are invariants, not conventions. Breaking one produces data that looks
fine and is not.

**Three-state booleans are real.** `is_full_time` and `is_expired` are TRUE,
FALSE *and* NULL, where NULL means "we do not know". Never test them for
truthiness — write `IS TRUE`, `IS FALSE` or `IS NULL`. A posting the scraper
re-sees is set FALSE because re-sighting is positive evidence it was live; one
never checked stays NULL, which is the truth.

**Duplicates are caught by fingerprint** — a 64-character hex SHA-256 of
company + title + location + experience. Seeing a posting again keeps
`first_seen_date`, bumps `last_seen_date` and increments `times_seen`.

**Open-ended vocabularies auto-register; fixed sets get a CHECK.** Skills,
degrees, departments and industries create a row the first time they appear.
`working_type` and `contract_type` are small fixed sets, so a CHECK constraint
is enough.

**Cities are the deliberate exception and must stay curated.** `cities.state`
cannot be guessed from a city name — and some names genuinely denote two places
in different states; `Srinagar` names one in Jammu & Kashmir and one in
Uttarakhand. So an unrecognised location is *surfaced* in `unmapped_locations`
rather than invented into the table.

26 cities are curated. 16 fragments are currently unmapped, and the important
point is that they are not one kind of thing — which is exactly why this cannot
be automated:

| | Examples | What to do |
| --- | --- | --- |
| Real Indian cities | Mohali, Salem, Surat, Patiala, Madhapur, Srinagar | curate, once someone supplies the state |
| Outside India | Dubai, Riyadh, Kenya, Overseas/International | out of scope for a table of Indian cities |
| A state, not a city | Tamil Nadu | wrong granularity for this column |
| Not places at all | Any Location, Anywhere in India/Multiple Locations, Metros, Others, pan india | **leave unmapped permanently** |

A future reader should expect this list to grow and should not treat its length
as a defect. An unmapped fragment is the system reporting honestly that it does
not know, which is the whole point.

**Every query is parameterised.** Use `WhereBuilder`. Anything that cannot be
parameterised — a sort column, a grouping dimension — is checked against an
allowlist. Never put caller input directly into SQL.

## 10a. One question, one expression — counting demand

A posting's demanded skills are `skill_ids || skill_group_ids(skill_groups)`.
`skill_ids` carries outright requirements; a skill the employer offered as one
of several alternatives sits in `skill_groups` and is deliberately not repeated
in `skill_ids`, because "wants AWS" and "would accept AWS" are different claims
and the schema keeps them separable.

Four places had the full expression from the start —
`snapshot_daily_skills()`, the `?skill=` filter on `/postings`, the
preferred-subset CHECK, and the GIN index. Eight read paths in `api/routers/`
did not, and nobody noticed for weeks because each endpoint was internally
consistent. The contradiction was only visible by asking two pages the same
question: the Skills chart said AWS appeared in 295 postings, the Jobs filter
returned 419 for the same skill, and Trends plotted 419. Corrected 2026-09-17.

**Not an `instrument_changes` row.** That ledger records changes to what gets
*recorded*, and `skill_daily_counts` was never affected — the snapshot function
was right all along. No series steps; the Skills page stops disagreeing with
history that was already correct. Filing it there would claim a discontinuity
that does not exist.

The lesson generalises: a rule enforced in four places and violated in eight is
not a rule, it is a convention. Where one expression defines a concept, the
expression belongs somewhere both sides read — a view, a function, or a
generated column — not copied into every query that needs it.

## 10b. A rename is a label fix, not a correction

`skill_daily_counts` stores the skill NAME, so a spelling entering
`SKILL_ALIASES` ends one series and starts another. 145 names holding 1,586
mentions were stranded this way by 2026-09-17, and `/trends/new-skills` was
presenting renames as new skills arriving in the market.

The instinct is to backfill the old names. That is wrong for the same reason
editing a wrong count is wrong: it destroys the evidence that anything changed,
leaving a continuous line and no explanation for it. `skill_renames` takes the
shape §9 already established — raw observation untouched, adjustment in its own
table, view applies it.

What makes it legitimate rather than a second corrections ledger is that the
two tables fix different things. A correction repairs a COUNT that was false
when written. A rename repairs a LABEL: the count was right before and after,
and only our index into it moved. Both claims in a snapshot row — *how many*
and *what we called it* — can fail independently, and conflating them under one
rule is what made this look unfixable at first.

**The 27-of-145 split is the honest part.** `posting_count` is
`COUNT(DISTINCT job_id)`, so two names are summable only when no posting could
have been counted under both — provable only when they share no snapshot date.
77 names ran in parallel rather than in sequence and are not recoverable:
Terraform and "Iac Terraform" were counted on the same days, with 22 of 23
postings holding both, so summing would report 83 where roughly 61 existed. The
true union cannot be derived from two distinct-counts, so the split stays
visible rather than being papered over with a plausible number. The remaining
41 have no canonical to map to at all.

## 11a. `skills.category` is a filter, not a label

`/analytics/skill-categories` reads `WHERE sk.category IS NOT NULL`. So NULL is
not an unfilled field — it is how a broad tag is kept out of the composition
chart, and the dashboard caption says as much: *"broad tags like 'Agile' or
'Communication Skills' are left out rather than lumped into 'Other'"*. About
88% of rows are NULL and that is the resting state, not a backlog.

This is easy to misread. Seeing 1,443 uncategorised skills and "fixing" them
would invert the filter: "Java Fullstack" (113 postings), "Cloud" (94) and
"Fullstack Development" (64) would enter a chart about *technical* composition
and plausibly dominate it. The existing values are all one axis — a layer of
the stack (Languages, Frontend, Backend, Database, Data/ML, Cloud/DevOps,
Testing). A value describing *what kind of term this is* belongs in a second
column, not this one.

**Two dictionaries, two migrations.** `categorize_skill()` runs only when a
skill is first registered, so a new `SKILL_CATEGORIES` key never reaches rows
already stored. And `normalize_skill()` falls through to an `_initcap`
fallback, so a name added to `SKILL_ALIASES` later collides with the row
already registered under the tidied spelling — adding an alias *without* a
merge migration widens the split rather than closing it. Both were done
together on 2026-09-16:

- 20 duplicate spellings merged. Ten folded into names that already carried a
  category (`Iac Terraform` → `Terraform`, `Snowflake Db` → `Snowflake`,
  `Data Build Tool` → `dbt`), closing a category gap as a side effect. Overlap
  was heavy — `Iac Terraform` had 23 postings and Terraform gained 1, because
  22 of them already held both ids. That is the cost of a split identity: one
  posting counted twice.
- 39 placeable technologies categorised, chosen from those used by 5+
  postings. Chart coverage 68.9% → 71.1% of skill mentions.
- `Ci/Cd Pipeline` (72 after merging three spellings) was **not** folded into
  `CI/CD` (385). That would be a judgement that two names mean one skill, not
  an observation that one skill was spelled twice — a different kind of claim.

`skill_daily_counts` is never touched by either. A merge changes future counts
only, which makes it an instrument change, not a correction — see §9.

## 12. Flagged, never hidden

Where the pipeline detects that something is probably wrong, it stores the
evidence alongside the data instead of rejecting or hiding it.

**Foreign descriptions.** Naukri sometimes serves a description belonging to a
different job — five rows currently hold an IoT/drone/Indore JD matching neither
their company nor their title. It is not a scraper bug: the logs record the
wrong text arriving already mined on six consecutive days, and a re-visit found
exactly one JD container per page.

The source cannot be adjudicated automatically — about half of what the check
catches is a recruiter legitimately naming another office in the body — so
nothing is ever rejected. `description_foreign_cities` carries the evidence,
refreshed on every sighting so it clears itself when Naukri corrects the text,
`?description_flagged=` filters on it, and the Jobs detail panel says so in
words.

**`naukri_role` is a recruiter-picked dropdown, not derived from the job
description.** Verifiably noisy: the same title gets different values, and it
occasionally contradicts the posting outright. It is stored because it is a real
signal, and it is never used as ground truth.

The pattern in both: the pipeline's job is to record what the source said,
including when the source contradicts itself. Deciding which version is true is
a separate question and a human one.

## 13. Two databases, because they are two populations

The Dassault-ecosystem pipeline writes to a separate database with an identical
schema. It could have been a third value in `cleaned_postings.source`. It is
not, because those postings are a different *population*, not a different job
board: CAD and PLM roles across every Indian city, against a fixed set of
Hyderabad IT searches. Mixing them would move every skill trend, salary band and
city ranking for reasons that have nothing to do with the market.

Same code, same schema, separate database, no shared row. A fix to the scraper
reaches both; a fix to one pipeline's data reaches neither.

That store lives in the `jd_scraped` schema of a hosted Railway database, and
three decisions there are deliberate:

- **`search_path = jd_scraped`, with no `public` fallback.** That database's
  `public` schema belongs to an unrelated project. A missing table must raise an
  error rather than resolve against someone else's data and quietly write there.
- **The password lives in `%APPDATA%\postgresql\pgpass.conf`**, never in code,
  the environment or a log. The collector explicitly clears any inherited
  `PGPASSWORD`, because the one set on this machine is for the local instances
  and sending it would both fail authentication *and* stop libpq consulting
  pgpass at all.
- **A preflight runs before the browser opens.** `save_records()` is the last
  thing a run does, so a wrong password or an unmigrated schema would otherwise
  be discovered only after a full scrape — and the scrape would be gone with it.
  Two seconds against ten minutes.

No `idle_in_transaction_session_timeout` is set there. A database-level setting
would apply to the other project sharing the server, and that is not ours to
change.

**Off-target rows are kept, not cleaned out.** Four of that store's search
terms returned roughly 80 postings that are not Dassault-ecosystem roles at all.
They were deliberately left in place: it is a store, not a curated set, and
deleting rows because a search term turned out to be poor would destroy the
record of which terms were tried. The term's measured score, recorded beside it
in the search list, is the useful artefact.

**If that store is ever run as pure storage**, six tables and six views can be
dropped without affecting a single write — `scrape_runs`, `liveness_runs`,
`hirist_liveness_observations`, `instrument_changes`, `skill_daily_corrections`
and `skill_daily_counts`, plus every trend view. This was verified by dropping
them and running the real `save_records()` against what remained. **Only
`skill_daily_counts` is unsafe**: every other table can be recreated empty with
nothing lost, but that one holds history that cannot be rebuilt — and dropping
it also requires removing the `snapshot_daily_skills()` call, or every run saves
its postings and then crashes.

## 14. The scraper is slow on purpose

One visible browser for the whole run and a random 3–6 second pause between
pages. Naukri blocks headless browsers outright. Do not go headless, do not
parallelise, do not remove the pause.

## 15. Search terms are measured, never reasoned about

Naukri's ranking cannot be predicted from the words in a term. Measured
9 September 2026 on page one:

```
catia-design-engineer   20 of 20 CATIA roles
cad-engineer            19 of 20
plm-consultant          20 of 20
catia-engineer           1 of 20   -- "Prompt Engineer", "React Native Engineer"
manufacturing-engineer   0 of 20
engineering-consultant   0 of 20
```

A term fails when one of its words is common enough to fill the page alone. Two
identically-shaped terms can score 19/20 and 1/20, so the shape of a term tells
you nothing.

**The URL form is not the lever.** The bare slug, `k=` with the phrase, `k=`
with the phrase quoted, and a generic path with a quoted `k=` all returned
byte-identical results. Quoting buys nothing. The query string *is* useful for
Naukri's own filters — `experience=` and `jobAge=` were each measured to change
the result set — which is what a search entry's third element carries.

So every candidate term is probed with `--probe` before it is added, and its
measured score is recorded beside it in the search list. `--probe` reads the
result cards only: no detail pages, no database, no writes, about twenty
seconds per term.

## 16. What was deliberately not built

- **No raw postings table.** The scraper cleans in memory and writes straight to
  the cleaned tables. One existed once and was removed.
- **No AI anywhere in the pipeline.** Extraction is DOM selectors; skills are
  regex. Missing labels become NULL. A model call to fill a gap would be a
  fabrication with better grammar.
- **No indexes added speculatively.** Several queries sequential-scan today; at
  920 rows a full scan is 416 pages and one millisecond, and an index would slow
  every write to speed up nothing. The five GIN indexes that *were* added
  speculatively were scanned zero times in the database's life and have now been
  removed.
- **Nothing normalised to save bytes.** Every repeated text column was measured:
  dictionary-ising all of them saves about 80 kB on a four-megabyte table — 2%.
  `title` at 1.6× reuse would have been actively wrong. Normalisation is for
  integrity and for facts that repeat, not for storage.

### Phase 2 of the split, designed and deferred

Three further moves are designed and deliberately not built:
`companies` / `searches` / `naukri_roles` dictionaries; `certifications` and
`unmapped_locations` as 1:N child tables; and retyping `fingerprint` from
64-character hex text to `bytea` (65 bytes to 33, with a smaller unique index).

Together they take the spine from 339 bytes to roughly 186. They were left out
because they are the modest-bytes tail — the measured wins are all in what phase
one does — and each adds a join to endpoints that currently need none.

## 17. Operational facts that have already cost time

Recorded because each was discovered the expensive way.

- **`VACUUM` cannot run inside a transaction block.** pgAdmin's Query Tool sends
  multiple highlighted statements as one batch, which makes them one implicit
  transaction — so `ALTER TABLE ... SET (fillfactor); VACUUM FULL ...;` run
  together fails, and **the `ALTER` rolls back with it**. Run them one statement
  at a time.
- **pgAdmin cannot run a stock `pg_dump` file.** pg_dump 18 wraps every dump in
  `\restrict` / `\unrestrict`, and the data itself arrives as `COPY ... FROM
  stdin` — both are psql client features, not server SQL. Dump with `--inserts`
  for pgAdmin. Stripping only `\restrict` produces a file that creates every
  table and loads no rows.
- **`pg_stat_*` counters reset**, and a reset makes "0 scans" meaningless as
  evidence. Two indexes read 72 scans one day and 0 the next with no change to
  either. To decide whether an index is usable, run `EXPLAIN` with
  `enable_seqscan = off` — that answers the question the counters only gesture
  at.
- **Judge an index by which table the query names, not by size.** Five GIN
  indexes on `cleaned_postings` looked load-bearing for scale. The only query
  that could have used them names `posting_skills`, and scale does not change
  which table a query names.
- **`schema.sql` drifts silently, and only a diff catches it.** By
  2026-09-15 it had drifted twice: no `expiry_basis` column, so a fresh install
  failed on its first save; and `hirist_liveness_observations` existed only in a
  migration, so a fresh install lacked a table the live database had. Neither
  showed up in any test, because the test suite runs against a database that was
  built by migrations rather than by `schema.sql`. Both are fixed — building
  from `schema.sql` + `trends_setup.sql` alone now yields 0 column differences
  against live, 25 tables and 49 indexes — but the lesson is the check, not the
  fix: **after any schema change, build from the two files and diff the result
  against the live database.**
- **Scheduled tasks are live.** `JobMarket` scrapes at 11:00 and `JobMarket
  Liveness Check` runs at 17:00, both enabled. A code change that expects a
  schema the database does not yet have will fail on the next firing — check
  `Get-ScheduledTaskInfo` for the next run before starting a migration.
- **Editing `trends_setup.sql` changes nothing until you re-run it.**
  `snapshot_daily_skills()` lives *in the database*; the file is only its
  source. During the 2026-09-15 split the file was patched to read
  `posting_sightings`, the function in the database still referenced the moved
  `cleaned_postings.last_seen_date`, and every test passed — because no test
  calls the function. It surfaced only because it was invoked by hand
  afterwards, and it would otherwise have lost a day of history that cannot be
  rebuilt. **Call the function, do not infer it from the file.**

- **The rollback for a schema change is a pre-migration dump, not a parallel
  schema.** Copying the old shape into a second schema alongside `public` was
  considered for the 2026-09-15 split and rejected: the pipeline writes to one
  place, so the copy is frozen at cutover and diverges with the next scrape.
  That makes it a snapshot — which is what a dump already is, without occupying
  the live database or risking a write to the wrong schema. The dump taken
  immediately before that migration
  (`Desktop\jobmarket_backup_pre_split_20260915_1728.sql`, 3.4 MB) was restored
  twice the same day with zero errors to verify the migration field by field,
  so it is a tested rollback rather than an assumed one.

### The rule these share

Each of the above is the same failure in a different costume: **the artefact
that runs is not the artefact you edited.** A `.sql` file is not the function,
a dump file is not the database, a stats counter is not the query plan, and a
patched module is not the schema. Verify by exercising the thing that actually
runs.

## 18. Where the rest of the record lives

This file and `CLAUDE.md` should be enough to work from. When more detail is
needed:

| | |
| --- | --- |
| `migrations/*.sql` | Each one's header states what it changed and why. `2026-09-15-split-cleaned-postings.sql` carries the full rationale for the four-table split. |
| `docs/superpowers/specs/` | Longer design records, including why the three skill columns were not merged into one. |
| Code comments | Deliberately carry the *reason* and the measurement, not the mechanics. `postings.py`'s skill filter and `liveness_checker.py`'s hirist note are the densest. |
| [jd_scraped schema guide](https://claude.ai/code/artifact/a7f67824-ba4d-473e-9362-64004fffd9c8) | The storage-only database: all 22 tables, the write path, operating commands. Also at `C:\Users\Acer\Desktop\jd_scraped_schema.docx`. |
| [cleaned_postings v2](https://claude.ai/code/artifact/aaec6f03-2ac3-49df-9a0d-926309d8c586) | The split design with the ER diagram and the complete column map. |

## 19. How the claims here were established

Every measurement came from one of three places: `EXPLAIN (ANALYZE, BUFFERS,
WAL)` against real or replicated rows; PostgreSQL's own statistics views; or a
throwaway database built from a dump of the live one, exercised with the real
`save_records()`, and dropped afterwards.

Where an earlier claim was wrong it has been corrected rather than quietly
dropped. Two worth knowing, because both were confidently stated first:

- The first HOT experiment changed two variables at once and credited the wrong
  one. The isolated 2×2 that followed showed the indexes contributed **nothing**
  when the indexed values were unchanged, and `fillfactor` was the whole effect.
  The indexes matter only when the indexed column genuinely changes — then they
  take HOT to 0% and no `fillfactor` can rescue it.
- The recommendation not to split was wrong, for the reason recorded in §4.

**The standing rule for this document: a number without a date and a method is a
guess. Do not add one.**
