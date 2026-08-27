# Skill column storage: measured and deferred

**Status:** decided — keep three columns. Revisit at the trigger below.
**Date:** 2026-08-27 (moved out of CLAUDE.md, which is an introduction)

Collapsing `skill_ids` + `preferred_skill_ids` + `skill_groups` into one column
keeps coming back as an idea. It has been measured properly;
`bench_skill_storage.py` reproduces the whole thing at ~100k rows. This is the
record so it does not have to be re-derived.

**Merging the three skill columns into one — measured and deferred, not unexamined.**
Collapsing `skill_ids` + `preferred_skill_ids` + `skill_groups` into a single column keeps
coming back as an idea. It has been measured properly; `bench_skill_storage.py` reproduces
the whole thing at ~100k rows. Summary so you don't have to re-derive it:

- *JSONB loses, in every arrangement.* An object `{"r":..,"c":..}` is +60% and 1.8× slower;
  a mixed array `[7, 273, [613,639]]` is +53%. JSONB stores each integer self-describingly
  and repeats key names in every row, where `INT[]` packs 4 bytes and names the column once
  in the catalog. Tagged encodings (`tag << 24 | skill_id`), 2-D NULL-padded arrays, and
  full normalization all lose too — anything needing a function to decode pays that function
  once per row, forever.
- *The one form that wins is negative sentinels in a plain `INT[]`*:
  `[required…, -1, preferred…, (-2, group)…]`. Every `skill_id` is a positive SERIAL, so
  negatives are unreachable and the array stays natively queryable — `&&`, `@>` and a GIN
  index directly on the column, no decode function. `-2` even becomes a searchable token, so
  "does this posting offer a choice" is an index lookup.
- *What it actually buys, against what is shipped today:* `?skill=` 4.7×, `?skills_all=` 45×,
  storage a wash (−1%). It does **not** speed everything up — the daily snapshot is ~10%
  *slower* merged, because the array holds occurrences where the old columns held sets, and
  the required `DISTINCT` costs more than the concatenation saved.
- *Its one sharp edge*, which cost two wrong numbers during the evaluation: **the array holds
  occurrences, not sets.** 48 postings name the same skill in more than one group (job 1364
  has Azure in four). `skill_group_ids()` is `array_agg(DISTINCT …)` and collapses them; a
  bare `unnest` does not. Any "how many postings" aggregate over the merged form needs
  `COUNT(DISTINCT job_id)` or it silently over-counts.
- *Writes can be made safe*; reads cannot. A `CHECK` calling one IMMUTABLE `wellformed()`
  refuses all 9 malformed shapes tried (missing `-1`, doubled `-1`, group before the divider,
  empty group, group of one, a skill both required and grouped, stray `-3`, a zero, empty
  array) and would enforce the disjointness that is currently only an application guarantee.
  No constraint can stop a correct array being *queried* wrongly, and `cardinality()`,
  `MIN`/`SUM`, and a forgotten `WHERE v > 0` all return plausible wrong answers.

**Trigger to revisit:** when the daily snapshot exceeds ~500 ms or `cleaned_postings` passes
~100k rows. It runs in ~4.5 ms today. Deferred because the gain is real but unrealised at
1.4 MB, and the current shape's self-describing property — `skill_ids` contains skill ids,
and the obvious reading is correct — is worth more than the storage until then. If it is ever
done, the `CHECK`, the unpacking view, and a test diffing all six statistics old-vs-new belong
in the *same* commit, not deferred behind it.

**Related, still open:** `?skills_all=` tests `(skill_ids || skill_group_ids(skill_groups)) @>`
and **no index covers that concatenation** — 350 ms at 100k rows versus 35 ms with one added.
Immaterial at today's size (~0.3 ms), and left unadded on the same reasoning as above, but it
is the first thing to fix at scale if the merge isn't done.

## Why this lives here and not in CLAUDE.md

CLAUDE.md is an introduction for someone new to the project. This is a closed
decision with a revisit trigger — useful when the trigger fires, noise until
then.
