# Descriptions that belong to a different posting

**Status:** diagnosed; detection shipped 2026-08-31. The five affected rows are
deliberately left as they are.
**Date:** 2026-08-31

## What happened

Five rows carry a 2,009-character JD for an *IoT Intern, drone technology,
Indore* — attached to Cisco Software Engineer postings at 7–12 years in
Hyderabad/Bengaluru/Chennai, and to Fractal Analytics Full stack Developer
postings at 2–5 years. Every field of the JD contradicts the posting: wrong
city, wrong seniority, wrong domain, and it contradicts itself ("3+ years of
experience or be a fresher").

| job_id | company | first→last seen | times_seen |
| --- | --- | --- | --- |
| 644 | Cisco | 12 Aug → 12 Aug | 1 |
| 645 | Cisco | 12 Aug → 13 Aug | 2 |
| 815 | Cisco | 14 Aug → 15 Aug | 2 |
| 713 | Fractal Analytics | 12 Aug → 13 Aug | 2 |
| 885 | Fractal Analytics | 14 Aug → 17 Aug | 6 |

## It is Naukri's data, not our scraping

The scrape logs print `tech_in_description` per posting, and
`extract_skills()` over the stored text gives exactly `C++, Java, Python,
SQL`. That line appears in six consecutive daily logs (12–17 Aug), so the
wrong text was in the page the scraper read. `description` and
`tech_in_description` come from the same `safe_text()` call in the same
statement, so they cannot diverge in cleaning or in the UPSERT.

Two mechanisms were ruled out by measurement rather than argument:

- **Carry-over from the previous page.** The record scraped immediately
  before each affected one holds its own correct JD (Creative Hands HR before
  the first Cisco; Tactica, `tech_in_description: EC2`, before Cloudgen).
- **`safe_text()` taking the first of several JD containers.** Re-visiting
  the three still-open URLs on 31 Aug found **exactly one** container
  matching the selector on every page. Cisco now serves the correct JD;
  Fractal still serves the drone JD, nineteen days on.

**It self-heals, which is why the damage is under-counted.** A good sighting
overwrites a bad one — `description = EXCLUDED.description`, no COALESCE. A
third company was hit and recovered: Cloudgen Systems (job 724) got the drone
JD on 12–14 Aug and a correct one afterwards. So four postings across three
companies are known to have been affected, and the five stuck rows are stuck
only because those postings stopped being re-surfaced by the searches before a
clean read arrived.

## Blast radius

Confined to the description and what is mined from it. The fingerprint reads
company, title, location and experience — all correct — so identity, dedup,
`first_seen_date`, `times_seen`, expiry and every count by company, role,
city, experience or closure are untouched.

What is wrong: the description itself, `responsibilities_text` /
`requirements_text` (computed from it at read time), one skill (`SQL`) mined
from the wrong text, and two fabricated choice sets in `skill_groups` —
`[C++, Python]` and `[C++, Python, Java]`, from "using Python, C++, or other
programming languages". Each occurs exactly five times in the table and all
five are these rows, so `/analytics/skill-choices` and
`/analytics/skill-flexibility` carry a five-posting phantom against a real
leader (AWS/Azure/GCP) at 24.

## Detection

`cleaning.foreign_cities()` — a description that names cities, none of them
the posting's own. Surfaced by `job_database.mismatched_descriptions()`,
printed at the end of every scrape, and returned by
`/analytics/scrape-health`.

Rules measured over all 548 postings with a description, against the five
known bad rows:

| Rule | Flags | Caught |
| --- | --- | --- |
| **Foreign city** | **12** | **5/5** |
| Description states an experience range disjoint from the posting's band | 10 | 0/5 |
| No word of the title appears in the description | 30 | 2/5 |
| Foreign city AND no title word | 2 | 2/5 |
| Foreign city AND description never names the company | 10 | 5/5 |

Foreign city alone was chosen. The `AND` forms look tempting and are worse:
the experience rule catches none of them, because "3+ years of experience"
overlaps a 7–12 band; the title rule loses three, because the drone JD
happens to contain "software" and "engineering". The company refinement holds
recall and cuts two false positives, but 338 of 548 descriptions never name
their employer, so it is a weak signal carrying its own failure mode — not
worth a second heuristic tuned on a single incident of five rows.

**It warns and never rejects.** Seven of the twelve flags are recruiters
naming a different primary location in the JD body than in Naukri's location
field (Accenture ×5, Infosys ×2) — real postings with real descriptions.
Storing NULL on a heuristic's say-so would lose them.

The sweep found no second case: every other flag, on either rule, is a
description that plainly belongs to its posting.

## Why the five rows were left alone

Repair was considered and declined. 644 and 645 are recoverable — Naukri
serves the correct Cisco JD today — but 885's source is still wrong, and 713
and 815 are expired, so their URLs no longer resolve. Any repair would
therefore be partial, and the rows are 5 of 548 with correct identity data.
They heal on their own if a search ever re-surfaces them.

## What this does not cover

Everything here follows from the check being one comparison: does the
description name cities, and none of the posting's own?

1. **A same-city mix-up is invisible — and that is the likely one.** 546 of
   548 postings include Hyderabad and 308 are Hyderabad-only, because the
   searches target it. So the most probable version of this bug is one
   Hyderabad JD landing on another Hyderabad posting, which produces no
   disagreement and no warning. The drone JD was caught only because it
   happened to be an Indore job.
2. **Only the 26 cities in `cities` plus `CITY_ALIASES` are recognised.** A
   JD for Nagpur, Lucknow or Singapore names no known city, so it reads
   identically to a JD naming no city at all: silence.
3. **A JD that names no city is invisible.** Nothing to compare.
4. **Only cities are compared.** Wrong seniority, wrong role, wrong employer
   and wrong industry all pass. The experience-disjoint and title-overlap
   rules were measured and rejected (0/5 and 2/5 against the known rows), so
   those angles are genuinely uncovered rather than covered elsewhere.
5. **Roughly half of what it reports is not a defect** — 7 of today's 12 are
   recruiters naming a different primary location in the body. Deliberate:
   it warns and never rejects. The risk is human, not technical — a list
   that is usually noise stops being read.
6. **It warned after the write, so the snapshot was already poisoned.**
   *Addressed 2026-08-31 — see "Correcting the snapshot" below.* The check
   now runs before `snapshot_daily_skills()`, and a poisoned day can be
   corrected through a ledger instead of being permanently wrong.
7. **No way to mark a flag reviewed.** The check scans the whole table every
   run, so the five bad rows and the seven benign ones reappear on every
   scrape indefinitely — two of the five are expired and can never heal.
   *Partly addressed 2026-08-31:* the list was ordered oldest-first and the
   printer capped at 10 lines against 12 existing flags, so a new occurrence
   would have been found and then hidden. Now ordered newest-first with the
   remainder counted. The acknowledgement gap itself stands.
8. **Cost scales with description bytes.** 23 ms to fetch, 129 ms to match at
   546 postings, matched in Python because `CITY_ALIASES` is the only place
   that knows "Bangalore" means Bengaluru. Past ~1 s, move the match into SQL
   with an alias table and a lateral join.

## The flag is stored, not just reported

Decided 2026-08-31, after review of the twelve flags showed none of them can
be adjudicated without a human reading the posting. Store and mark; never
reject, never hide.

`cleaned_postings.description_foreign_cities TEXT[]` — the cities the
description names when it names none of the posting's own. Empty means
checked and clean. Mirrors `unmapped_locations`, which surfaces a different
backlog the same way.

Four properties this shape buys:

- **Written by `clean_record()`**, in the same statement as the description it
  describes, so the flag and the text can never disagree. A later sweep could.
- **In the UPDATE clause of `UPSERT_SQL`**, so it recomputes on every sighting
  and clears itself the moment Naukri serves the right description — which is
  exactly what happened to Cloudgen Systems unaided. Verified by round-trip:
  insert flagged, re-save clean → `[]`, re-save bad → `['Indore']`.
- **Storing derived data deliberately**, against this project's usual habit of
  recomputing it. `mismatched_descriptions()` now reads the column rather than
  rescanning, which is both consistent and 6 ms against 152 ms. The reason to
  break the rule is that a flag nobody can query is not a flag.
- **Nothing is filtered out by default.** `?description_flagged=true|false`
  exists to review, and a flagged posting appears in ordinary results
  unchanged: 12 flagged + 545 clean = 557 total.

## Correcting the snapshot

`skill_daily_counts` is the one table here that cannot be rebuilt —
`snapshot_daily_skills()` reads `cleaned_postings`, which only ever shows the
present — so a day recorded from bad input stays wrong forever. Measured
damage, distinct affected postings per day taken from the run logs:

| Date | C++ recorded | Affected | C++ true |
| --- | --- | --- | --- |
| 10 Aug | 1 | 0 | 1 |
| 11 Aug | 1 | 0 | 1 |
| 12 Aug | **9** | 4 | 5 |
| 13 Aug | **4** | 3 | 1 |
| 14 Aug | **3** | 3 | **0** |
| 15 Aug | **3** | 2 | 1 |
| 16 Aug | 2 | 1 | 1 |
| 17 Aug | **3** | 1 | 2 |

Read as a person reads the Trends page, C++ demand went 1 → 9 in a day and
decayed over a week. None of it happened. Java, Python and SQL were inflated
by the same counts but are large enough to absorb it (Python 85 against a
true 81); C++ is where a small absolute error becomes a false story.

**Two changes, 2026-08-31:**

1. **Order.** `_report_mismatched_descriptions()` now runs *before*
   `_snapshot_today()`. It prevents nothing on its own, but a warning that
   arrives after the irreversible write is a warning about nothing.
2. **`skill_daily_corrections`** — a ledger, never an edit. The raw row stays
   exactly as observed; a correction is a separate, reasoned row, and deltas
   sum so a day can carry several. `skill_daily_counts_corrected` applies
   them and exposes `raw_count` and `correction` alongside, so what was seen
   and what was judged stay separable. `skill_delta_daily`,
   `skill_delta_vs_baseline` and `skill_first_appearances` read the corrected
   view; `snapshot_coverage` deliberately reads the raw table, because "which
   days were recorded" is not changed by a correction.

24 corrections were applied for 12–17 Aug (6 days × 4 skills), guarded by a
transaction that aborts if any count would go negative. A skill corrected to
zero drops out of the view entirely, which is what the snapshot would have
held had the bad postings never been written — so 14 Aug now has no C++ row,
and `days_since_previous` correctly reports a two-day gap across it rather
than inventing a value.

**Not fixed by this:** the corrections were reconstructable only because the
scrape logs record `tech_in_description` per posting. There is no stored
record of which postings fed which snapshot day, so a future incident is
correctable only if the logs still exist. Storing the
`(snapshot_date, job_id, skill_id)` grain — roughly 2.7M rows a year — would
make any past day recomputable rather than merely annotatable. Deferred: at
today's volume the ledger plus the logs is enough.

**The untested idea most likely to close gap 1:** compare the skills mined
from the description against Naukri's own key-skill chips. In the drone case
those disagreed completely — chips said `copilot, cursor, clean code`, the
text yielded `Python, Java, C++` — and that signal does not depend on cities
at all. Its false-positive rate is unmeasured; measure before trusting.

Also open: **whether Naukri ever fixes 885**, worth a look before assuming
the five rows are permanent.
