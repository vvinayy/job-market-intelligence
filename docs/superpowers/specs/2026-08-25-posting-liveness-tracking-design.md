# Posting liveness tracking

**Status:** implemented 2026-08-27, plus a first metric
(`/analytics/closures`). Time-based metrics still deferred pending history.
**Date:** 2026-08-25

## Why

A posting is only updated when a *search* re-surfaces it. `naukri_collector.py`
contains no database reads — its whole worklist comes from
`discover_job_urls(page, search_url, limit)`. An expired posting drops out of
search results, so it can never be re-surfaced, so it can never be corrected or
marked. The rows that most need updating are exactly the ones the current design
cannot reach.

Fixing that also buys a measurement: knowing when each posting closed is the
closest thing this dataset has to a hiring signal.

## What we can observe

Naukri soft-redirects an expired posting's URL to a search page carrying
`expJD=true`. Verified against 370 of the 495 stored URLs: 60 expired, 0 errors,
0 pages fitting neither state. HTTP status stays `200` either way, so the status
code is useless — the redirect is the signal.

Two limits worth stating once:

- **We never learn why it closed.** Filled, withdrawn and expired-unfilled are
  identical from outside. Nothing here may be named "hires" or "hiring rate";
  the honest word is **closed**.
- **Absence is not expiry.** Measured: mean `last_seen` day is 10.6 for expired
  postings and 12.7 for live ones. Staleness barely predicts death, which is why
  this needs a direct check rather than an inference rule.

## Scope

**In:** record, every day, whether each posting's URL is still live.

**Out:** the metrics. Recording the raw observations is deliberately separated
from deciding what to compute — the data supports many framings and none of them
has to be chosen now. Also out: backfilling the existing 495, which were
discovered mid-life with check gaps up to 19 days.

---

## Design

### Detection

```
expired  ⟺  302/301/303/307/308  AND  Location carries expJD=true
live     ⟺  200 at the requested URL
unknown  ⟺  anything else
```

`liveness.py` holds both readings of this rule: `classify_response()` for the
HTTP path the checker uses, and `classify()` for a rendered page, which the
scraper uses mid-run. Both are pure and tested; the browser form also replays
the full 495-page census to 62 expired / 433 live with zero disagreements.

**Expiry requires positive evidence.** A timeout, network error, block page or
unrecognised layout is `unknown`, never `expired`. Without this, one Naukri block
would mass-mark live postings dead and the damage would be indistinguishable
from a real market event afterwards.

`unknown` writes no row at all — same as not having checked, which is what it is.

### Storage

Three columns on `cleaned_postings`, not a check-log table. A log was drafted
and dropped: the metrics are deferred, so nothing yet needs per-day
granularity, and the columns answer "is it dead" and "when did we find out"
without one.

```sql
is_expired       BOOLEAN,   -- NULL = never checked
expired_on       DATE,      -- first date we confirmed it
last_checked_on  DATE
CONSTRAINT cleaned_postings_expired_on_requires_expired
    CHECK (expired_on IS NULL OR is_expired IS TRUE)
```

Three states, not two. NULL must not collapse into "live" -- the mistake
`normalize_working_type()` made on 372 rows. Same shape as `is_full_time`;
never test for truthiness.

`expired_on` is the date **we confirmed it**, not the date Naukri closed it,
which is unobservable. `COALESCE(expired_on, CURRENT_DATE)` on update, so
re-confirming a dead posting does not push the date forward daily. The gap to
`last_checked_on` is the measurement error, readable per row.

Measured against one JSONB column at 100k rows: three columns 4.4 MB, one
jsonb 11 MB (**149% larger**), and JSONB would also cost a date type, a
readable CHECK, a direct index, and plain assignment in the UPSERT.

`last_seen_date` keeps its meaning -- "a search surfaced this" -- and is
untouched by the checker. Conflating it with "we verified the URL" would
destroy the ability to tell scraper coverage from direct verification.

### UPSERT change

A successful scrape is proof of life, so ordinary scraping maintains this for
free on every row search reaches. Added to the SET clause of `UPSERT_SQL`:

```sql
is_expired      = FALSE,
expired_on      = NULL,
last_checked_on = CURRENT_DATE
```

Literals, not `EXCLUDED.x` -- derived from the scrape having succeeded, not
scraped. `last_seen_date = CURRENT_DATE` in the same clause is the precedent.

`naukri_collector.py` also checks for `expJD` immediately after `goto()`,
before its 20s description wait. That catches a posting expiring mid-run,
saves the timeout, and separates `[expired]` from `[skip] Description never
rendered` -- which now means "the selector broke" and nothing else.

### The checker

`liveness_checker.py`. Reads URLs from the database -- the one thing the
collector has never done -- and writes only the three columns above.

**HEAD request, no browser.** Naukri answers an expired posting with a 302
whose `Location` carries `expJD=true`, so nothing needs rendering. Measured
against the full 495-URL browser census: **40/40 agreement** on a 40-URL
sample, the single apparent disagreement being a posting that genuinely
expired between the two runs (confirmed by re-checking it with a browser).

Strategies measured, per check:

| Strategy | Time | Correct | Browser |
|---|---|---|---|
| Full render | 0.85s | yes | yes |
| Subresources blocked | 5.76s | **no** | yes |
| `page.request.get` | 0.59s | yes | yes |
| GET follow-redirects | 0.16s | yes | no |
| **HEAD no-follow** | **0.11s** | **yes** | **no** |

Consequences: ~10 min for the full table instead of ~35, no page bodies
downloaded, and **no interactive-logon requirement** -- a headed browser was
the only reason one existed.

Throttle is 0.8-1.5s rather than the scraper's 3-6s. Not a weakening: a
rendered page pulls ~40 subrequests every 4.5s (~9 req/s in bursts), where
this makes exactly one request per second. Still randomised, still serial.

Work queue, so cadence is a WHERE clause and a tiered policy needs no
migration:

```sql
WHERE is_expired IS NOT TRUE
  AND (last_checked_on IS NULL OR last_checked_on < CURRENT_DATE)
ORDER BY last_checked_on ASC NULLS FIRST
```

Scheduled as `JobMarket Liveness Check`, 5pm daily, running
`jobmarket.bat --check-only`. Off the morning path so it cannot delay the
dashboard launch.

**Known risk:** if Naukri moves to a JS-driven redirect, HEAD would read an
expired posting as live. Re-verify against a browser run before trusting a
sudden drop in the expiry rate.

### Safety valves

Two, and both abort the whole run writing nothing rather than partial results:

- **>30% expired** -- "everything died". A third of the board closing in one
  day does not happen; a block or a layout change looks exactly like it.
- **>25% inconclusive** -- "nothing answered". Catches being blocked, which
  the first valve would miss.

Both skip runs under 20 postings, so a `--limit 3` smoke test never trips them.

Underneath both: **expiry needs positive evidence.** A 403, 429, timeout, 404
or a redirect elsewhere is `unknown` and writes nothing. Without that, one
block writes off the table and is afterwards indistinguishable from a real
market event.

## Metrics: deliberately deferred

`expired_on` plus `last_checked_on` records both ends of the interval, so the
measurement error is visible per row and any framing can be computed later.
Four things to carry forward when that happens:

1. **Use `lifelines`.** Averaging only closed postings ranks categories
   *backwards* — slow closers are disproportionately still open and get excluded
   from the average, so a category with many open postings looks faster. Survival
   analysis with right-censoring is the correct tool, and `lifelines` is the
   small library that does it properly.
2. **Cell sizes decide which groupings work.** A stable median wants ~30 closures
   per group; at a 17% closure rate that is ~150 postings per group. Today only
   `skill` clears that, and only for its top values. `city` and `company` are out
   on structure — city's largest group is 493 of 495, since the searches target
   Hyderabad.
3. **Two populations must not be averaged together.** Postings caught near
   posting date have a measurable time-to-close; postings caught mid-life do not.
   Distinguishable from `posted_date` and `posted_raw`, both already stored.
4. **The existing 495 are the truncated cohort.** They were first checked on
   2026-08-27 after being discovered over Aug 6-25, so their `expired_on` says
   only "dead by the 27th" — an uncertainty band up to 19 days wide. Postings
   discovered from now on are checked daily from discovery and carry a
   one-day band. Never average the two.

---

## Testing

`tests/test_liveness.py` — 19 pure-function tests, no I/O. The negative cases
carry the weight: 403, 429, 5xx, 404, a redirect elsewhere, a missing Location
and `expJD=false` must all resolve to `unknown`, because "expired" writes off a
row permanently.

The strongest check is not a fixture: the browser classifier replays all 495
recorded census pages to 62 expired / 433 live with zero disagreements, and the
HTTP classifier agrees with the browser 40/40.

Done manually, since nothing covers it:

- Checker run against known-expired and known-live rows; confirmed the three
  columns written and `last_seen_date` untouched.
- `jobmarket.bat --bogus` prints the updated usage; file stays ASCII + CRLF.

## Rollout — done 2026-08-27

1. ✅ Migration: three columns, CHECK, partial index; `schema.sql` matches.
2. ✅ UPSERT change, so ordinary scrapes maintain state for free.
3. ✅ `liveness_checker.py`, smoke-tested at `--limit 3` and `--limit 20`.
4. ✅ `jobmarket.bat --check-only`; scheduled task at 5pm daily.
5. ✅ `naukri_collector.py` detects `expJD` before its 20s wait.
6. ✅ `/analytics/closures` — closure rate by experience band or role,
   exposure-adjusted. Only those two dimensions: both are fingerprint fields
   captured since the first scrape, so neither carries the cohort confound.
7. ⬜ Time-based metrics (survival curves, net demand, market pulse) once
   `expired_on` spans more than one date.

## Open questions

1. **Safety-valve threshold.** 30% is a guess.
2. **Do closed postings leave the existing analytics?** Not decided here — it
   changes every number on the dashboard and deserves its own decision once the
   steady-state closed share is known. Trend history is unaffected either way;
   `snapshot_daily_skills()` already filters to `last_seen_date = CURRENT_DATE`.
3. **Retention.** 180k rows/year is fine indefinitely at this scale; revisit only
   if the table becomes large enough to matter.
