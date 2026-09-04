"""Re-derive skills for postings extracted before the 2026-09-02 detector.

extract_skills() was rewritten and fourteen vocabulary entries added on
2026-09-02 (96ada74). Everything scraped since carries that vocabulary; 464
Naukri rows last seen before it do not, and are missing whatever the new
entries would have found in their descriptions.

Unlike the hirist backfill this needs NO network. `description` is stored, so
the description-mined half can be recomputed locally. What cannot be recomputed
is the other half -- Naukri's own skill chips are merged into skill_ids at
write time and never stored separately -- so this UNIONS rather than replaces:

    new = stored  U  extract_skills(description)

That is the right shape for a vocabulary extension, which only ever adds. It
was verified on the hirist population, where the same change gained 24 skills
and lost none. A union also means the chips cannot be silently dropped by a
re-derivation that never saw them.

Choice groups ARE recomputed, since find_skill_choice_groups() reads the
description and the skill list, both of which this has in full.

RUN THE SPELLING MERGES FIRST -- 2026-09-04-merge-duplicate-jpa-skill.sql and
2026-09-04-merge-stale-skill-spellings.sql. Fresh extraction emits canonical
names ('LLM', 'Generative AI', 'Data Pipeline'); with the superseded rows
still present this would widen those splits instead of closing them.

WHAT IT TOUCHES: skill_ids and skill_groups, on cleaned_postings and
posting_skills.

WHAT IT DOES NOT TOUCH: preferred_skill_ids (Naukri's starred chips, which
cannot be recomputed and must survive), last_seen_date and times_seen (a
re-derivation is not a sighting), and skill_daily_counts (those rows record
what the pipeline saw on the day, which was genuinely the old vocabulary).

    python migrations/2026-09-04-rederive-stale-vocabulary-skills.py
    python migrations/2026-09-04-rederive-stale-vocabulary-skills.py --apply
"""

import os
import sys
from collections import Counter

import psycopg2
from psycopg2.extras import Json, execute_values

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cleaning                                          # noqa: E402
from skill_taxonomy import extract_skills, find_skill_choice_groups   # noqa: E402

CUTOFF = "2026-09-02"


def connect():
    return psycopg2.connect(
        dbname=os.environ.get("PGDATABASE", "jobmarket"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=os.environ.get("PGPORT", "5432"),
    )


def main(apply: bool) -> int:
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT skill_name, skill_id FROM skills")
        name_to_id = dict(cur.fetchall())
        id_to_name = {v: k for k, v in name_to_id.items()}
        cur.execute("SELECT skill FROM skill_blocklist")
        blocklist = {r[0] for r in cur.fetchall()}
        cur.execute(
            """SELECT job_id, description,
                      skill_ids || skill_group_ids(skill_groups) AS all_ids
                 FROM cleaned_postings
                WHERE last_seen_date < %s AND description IS NOT NULL
                ORDER BY job_id""",
            (CUTOFF,),
        )
        rows = cur.fetchall()

    print(f"{len(rows)} posting(s) last seen before {CUTOFF}.\n")

    plans, gained = [], Counter()
    for job_id, description, all_ids in rows:
        stored = {id_to_name[i] for i in (all_ids or []) if i in id_to_name}
        found = set(extract_skills(description))
        merged = stored | found
        if merged == stored:
            continue
        new = merged - stored
        gained.update(new)
        groups = [tuple(sorted(g)) for g in
                  find_skill_choice_groups(description, sorted(merged), blocklist)]
        plans.append((job_id, sorted(merged), groups, sorted(new)))

    print(f"{len(plans)} posting(s) would change, "
          f"{sum(gained.values())} skill mentions gained.\n")
    print("most common additions:")
    for name, n in gained.most_common(15):
        print(f"   {n:>4}  {name}")

    if not apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply.")
        conn.close()
        return 0

    try:
        with conn:
            with conn.cursor() as cur:
                missing = sorted({s for _, names, _, _ in plans
                                  for s in names if s not in name_to_id})
                if missing:
                    execute_values(
                        cur,
                        "INSERT INTO skills (skill_name, category) VALUES %s "
                        "RETURNING skill_id, skill_name",
                        [(n, cleaning.categorize_skill(n)) for n in missing],
                        fetch=True)
                    name_to_id.update({n: i for i, n in cur.fetchall()})
                    print(f"\nregistered {len(missing)} new skill(s)")

                for job_id, names, groups, _ in plans:
                    gids = [sorted({name_to_id[s] for s in g if s in name_to_id})
                            for g in groups]
                    gids = [g for g in gids if len(g) > 1]
                    grouped = {i for g in gids for i in g}
                    outright = sorted(name_to_id[s] for s in names
                                      if name_to_id[s] not in grouped)
                    # preferred_skill_ids is deliberately absent from both
                    # statements: Naukri's starred chips cannot be recomputed.
                    cur.execute(
                        "UPDATE cleaned_postings SET skill_ids = %s, skill_groups = %s "
                        " WHERE job_id = %s", (outright, Json(gids), job_id))
                    cur.execute(
                        "UPDATE posting_skills SET skill_ids = %s, skill_groups = %s "
                        " WHERE job_id = %s", (outright, Json(gids), job_id))

                for label, sql in (
                    ("dangling skill references", """
                        SELECT COUNT(*) FROM (SELECT unnest(skill_ids
                               || skill_group_ids(skill_groups)
                               || preferred_skill_ids) AS sid
                          FROM cleaned_postings) x
                         WHERE NOT EXISTS (SELECT 1 FROM skills s WHERE s.skill_id = x.sid)"""),
                    ("rows breaking the preferred-subset CHECK", """
                        SELECT COUNT(*) FROM cleaned_postings
                         WHERE NOT (preferred_skill_ids
                                    <@ (skill_ids || skill_group_ids(skill_groups)))"""),
                    ("rows where the two tables disagree", """
                        SELECT COUNT(*) FROM cleaned_postings c
                          JOIN posting_skills ps ON ps.job_id = c.job_id
                         WHERE c.skill_ids <> ps.skill_ids
                            OR c.skill_groups <> ps.skill_groups"""),
                    ("groups overlapping skill_ids", """
                        SELECT COUNT(*) FROM cleaned_postings
                         WHERE skill_ids && skill_group_ids(skill_groups)"""),
                ):
                    cur.execute(sql)
                    bad = cur.fetchone()[0]
                    if bad:
                        raise RuntimeError(f"{bad} {label} -- rolling back")
    except Exception as exc:
        print(f"FAILED, nothing written: {exc}")
        conn.close()
        return 1

    print(f"Applied to {len(plans)} posting(s).")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
