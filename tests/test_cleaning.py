"""Unit tests for cleaning.py's pure functions. No database, no network —
these run in milliseconds and are the first line of defense against the
kind of selector/logic regressions that have hit this project before
(Industry Type grabbing the wrong span, applicant count losing the
+/less-than direction)."""

import cleaning


# ---------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------
def test_fingerprint_is_deterministic():
    a = cleaning.make_fingerprint("Acme Corp", "Python Developer", "Hyderabad", "2-4 years")
    b = cleaning.make_fingerprint("Acme Corp", "Python Developer", "Hyderabad", "2-4 years")
    assert a == b


def test_fingerprint_ignores_case_and_whitespace():
    a = cleaning.make_fingerprint("Acme Corp", "Python Developer", "Hyderabad", "2-4 years")
    b = cleaning.make_fingerprint("  ACME   corp", "python   developer", "HYDERABAD", "2-4 years")
    assert a == b


def test_fingerprint_changes_with_experience():
    # Deliberate: two openings at the same company/title/city but
    # different experience bands must NOT collapse into one fingerprint.
    a = cleaning.make_fingerprint("Acme Corp", "Python Developer", "Hyderabad", "0-2 years")
    b = cleaning.make_fingerprint("Acme Corp", "Python Developer", "Hyderabad", "5-8 years")
    assert a != b


# ---------------------------------------------------------------------
# Seniority — priority ordering is the part most likely to regress
# silently if a new pattern is added carelessly.
# ---------------------------------------------------------------------
def test_seniority_no_marker_returns_none():
    assert cleaning.classify_seniority("Python Developer") is None
    assert cleaning.classify_seniority(None) is None


def test_seniority_plain_senior():
    assert cleaning.classify_seniority("Senior Python Developer") == "Senior"


def test_seniority_project_manager_beats_generic_manager_pattern():
    # "manager" alone is priority 15; "project manager" is priority 11
    # and must win for a title containing both substrings.
    assert cleaning.classify_seniority("Project Manager - Cloud") == "Manager/Leadership"


def test_seniority_technical_lead_not_confused_with_bare_lead():
    # "technical lead" (priority 10) must be checked before the generic
    # "lead" (priority 20) — both are substrings of this title.
    assert cleaning.classify_seniority("Technical Lead, Backend") == "Lead/Principal"


def test_seniority_fresher_and_intern():
    assert cleaning.classify_seniority("Fresher - Trainee Engineer") == "Intern/Trainee"


# ---------------------------------------------------------------------
# Role family
# ---------------------------------------------------------------------
def test_role_unmatched_title_is_other():
    assert cleaning.classify_role("Chief Vibes Officer") == "Other"


def test_role_none_title_is_other():
    assert cleaning.classify_role(None) == "Other"


# ---------------------------------------------------------------------
# Range parsing
# ---------------------------------------------------------------------
def test_parse_range_min_max_normal_range():
    assert cleaning.parse_range_min("6 - 10 years") == 6.0
    assert cleaning.parse_range_max("6 - 10 years") == 10.0


def test_parse_range_plus_has_no_upper_bound():
    # "5+ years" -- don't invent an upper bound that isn't in the source.
    assert cleaning.parse_range_min("5+ years") == 5.0
    assert cleaning.parse_range_max("5+ years") is None


def test_parse_range_not_disclosed_is_none():
    assert cleaning.parse_range_min("Not Disclosed") is None
    assert cleaning.parse_range_max("Not Disclosed") is None


def test_parse_range_none_input():
    assert cleaning.parse_range_min(None) is None
    assert cleaning.parse_range_max(None) is None


# ---------------------------------------------------------------------
# Employment / contract / working type
# ---------------------------------------------------------------------
def test_employment_and_contract_type_split_on_comma():
    # Naukri packs both facts into one comma-separated field, and each
    # parser must pick out only its own half.
    assert cleaning.parse_is_full_time("Full Time, Permanent") is True
    assert cleaning.parse_contract_type("Full Time, Permanent") == "Permanent"


def test_part_time_is_false_not_none():
    """False and None mean different things: Naukri said part time, versus
    Naukri said nothing. A falsy check would collapse the two."""
    assert cleaning.parse_is_full_time("Part Time, Contract") is False
    assert cleaning.parse_is_full_time("part-time") is False


def test_employment_type_no_match_is_none():
    assert cleaning.parse_is_full_time("Something Unexpected") is None
    assert cleaning.parse_is_full_time(None) is None


def test_working_type_no_badge_is_unknown_not_onsite():
    """No badge means Naukri said nothing, not that the job is on-site.

    This asserted "On-site" until 2026-08-25, which put a fabricated value on
    372 of 495 rows — the badge only renders when a remote arrangement exists.
    """
    assert cleaning.normalize_working_type(None) is None
    assert cleaning.normalize_working_type("") is None
    assert cleaning.normalize_working_type("not found") is None


def test_working_type_reads_a_real_badge():
    assert cleaning.normalize_working_type("Hybrid work mode") == "Hybrid"
    assert cleaning.normalize_working_type("Work from home") == "Remote"
    assert cleaning.normalize_working_type("Permanently remote") == "Remote"
    # Only an explicit office badge yields On-site.
    assert cleaning.normalize_working_type("Work from office") == "On-site"


# ---------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------
def test_resolve_locations_maps_known_aliases():
    city_map = {"Hyderabad": 1, "Bengaluru": 2}
    ids, unmapped = cleaning.resolve_locations("Hyderabad, Bangalore", city_map)
    assert set(ids) == {1, 2}
    assert unmapped == []


def test_resolve_locations_keeps_unmapped_fragments_visible():
    city_map = {"Hyderabad": 1}
    ids, unmapped = cleaning.resolve_locations("Hyderabad, Atlantis", city_map)
    assert ids == [1]
    assert unmapped == ["Atlantis"]


def test_resolve_locations_none_input():
    ids, unmapped = cleaning.resolve_locations(None, {})
    assert ids == []
    assert unmapped == []


def test_resolve_locations_strips_parenthetical_locality():
    # Real fragment seen in production: "Hyderabad( Raidurgam )" —
    # Naukri appends a locality in parens that must be stripped before
    # the alias lookup, or the fragment falls through to unmapped.
    city_map = {"Hyderabad": 1}
    ids, unmapped = cleaning.resolve_locations("Hyderabad( Raidurgam )", city_map)
    assert ids == [1]
    assert unmapped == []


# ---------------------------------------------------------------------
# Descriptions that belong to another posting
# ---------------------------------------------------------------------
DRONE_JD = (
    "We are looking for a highly motivated and enthusiastic IoT Intern to "
    "join our team in Indore, specializing in Drone Technology. Develop and "
    "implement algorithms using Python, C++, or other programming languages."
)


def test_foreign_cities_flags_a_description_from_another_posting():
    # The real case: Naukri served this JD on Cisco and Fractal postings in
    # Hyderabad for six days. Every other field was correct, so this city
    # disagreement was the only observable difference.
    assert cleaning.foreign_cities(DRONE_JD, ["Bengaluru", "Hyderabad"]) == ["Indore"]


def test_foreign_cities_silent_when_the_description_agrees():
    text = "Hybrid role based in Hyderabad, with occasional travel to Indore."
    assert cleaning.foreign_cities(text, ["Hyderabad"]) == []


def test_foreign_cities_matches_aliases_not_just_canonical_names():
    # A posting in Bengaluru whose description says "Bangalore" agrees; the
    # alias table is the only place that knows the two are one city.
    assert cleaning.foreign_cities("Office is in Bangalore.", ["Bengaluru"]) == []
    assert cleaning.foreign_cities("Office is in Bangalore.", ["Hyderabad"]) == ["Bengaluru"]


def test_foreign_cities_needs_a_city_on_both_sides():
    # No city named, or none resolved on the posting, means nothing to
    # compare — silence, not a flag.
    assert cleaning.foreign_cities("Remote, anywhere in India.", ["Hyderabad"]) == []
    assert cleaning.foreign_cities(DRONE_JD, []) == []
    assert cleaning.foreign_cities(None, ["Hyderabad"]) == []


def test_clean_record_carries_the_flag_onto_the_posting():
    # The flag must be written in the same statement as the description it
    # describes, so the two can never disagree. A flagged posting is still
    # cleaned in full -- this marks, it never rejects.
    raw = {"title": "Software Engineer", "company": "Cisco",
           "location": "Hyderabad", "experience": "7 - 12 years",
           "description": DRONE_JD}
    cleaned = cleaning.clean_record(raw, {"Hyderabad": 1}, set())
    assert cleaned["posting"]["description_foreign_cities"] == ["Indore"]
    assert cleaned["posting"]["description"] == DRONE_JD
    assert cleaned["posting"]["company"] == "Cisco"


def test_clean_record_leaves_the_flag_empty_when_nothing_conflicts():
    raw = {"title": "Software Engineer", "company": "Cisco",
           "location": "Hyderabad", "experience": "7 - 12 years",
           "description": "Backend work in Hyderabad on Python services."}
    cleaned = cleaning.clean_record(raw, {"Hyderabad": 1}, set())
    assert cleaned["posting"]["description_foreign_cities"] == []


def test_foreign_cities_does_not_match_inside_a_longer_word():
    # "Punejobs" is not Pune. Word boundaries, or common substrings would
    # flag postings at random.
    assert cleaning.foreign_cities("Punelike conditions apply.", ["Hyderabad"]) == []


# ---------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------
def test_merge_skills_dedupes_and_normalizes():
    result = cleaning.merge_skills(["python", "AWS"], ["Python", "Docker"])
    assert result == sorted(set(result))  # no duplicates
    assert "Docker" in result
    assert result.count("Python") == 1


def test_merge_skills_handles_none_sources():
    assert cleaning.merge_skills(None, None) == []


def test_categorize_skill_known_and_unknown():
    assert cleaning.categorize_skill("Python") == "Languages"
    assert cleaning.categorize_skill("Some Totally Novel Tool") is None


# ---------------------------------------------------------------------
# clean_record — the single entry point, exercised end-to-end
# ---------------------------------------------------------------------
def _raw_posting(**overrides) -> dict:
    base = {
        "title": "Senior Python Developer",
        "company": "Acme Corp",
        "experience": "5 - 8 years",
        "salary": "15 - 25 Lacs P.A.",
        "location": "Hyderabad",
        "key_skills": ["Python", "AWS"],
        "tech_in_description": ["Docker"],
        "preferred_key_skills": ["Python"],
        "description": "Looking for a Python developer with AWS experience.",
        "working_type": "Hybrid",
        "employment_type": "Full Time, Permanent",
        "role_category": "not found",
        "naukri_role": "not found",
        "industry_type": "IT Services & Consulting",
        "department": "Engineering - Software & QA",
        "posted_date": None,
        "posted_raw": "3 days ago",
        "openings": 2,
        "applicant_count": 44,
        "company_rating": "4.2",
        "company_reviews": 120,
        "company_badges": ["Great Place to Work"],
        "source_search": "https://www.naukri.com/python-developer-jobs",
        "url": "https://www.naukri.com/job/123",
    }
    base.update(overrides)
    return base


def test_clean_record_end_to_end_shape():
    result = cleaning.clean_record(_raw_posting(), city_name_to_id={"Hyderabad": 1})
    posting = result["posting"]

    assert posting["title"] == "Senior Python Developer"
    assert posting["seniority_level"] == "Senior"
    assert posting["experience_min"] == 5
    assert posting["experience_max"] == 8
    assert posting["city_ids"] == [1]
    assert posting["working_type"] == "Hybrid"
    assert posting["is_full_time"] is True
    assert posting["contract_type"] == "Permanent"
    assert "Python" in result["skills"]
    assert "Docker" in result["skills"]


def test_clean_record_not_found_sentinel_becomes_none():
    # role_category/naukri_role are "not found" in the fixture above --
    # _clean() must turn that into a real None, not keep the sentinel.
    result = cleaning.clean_record(_raw_posting(), city_name_to_id={"Hyderabad": 1})
    assert result["posting"]["role_category"] is None
    assert result["posting"]["naukri_role"] is None


def test_clean_record_preferred_skills_is_subset_of_skills():
    result = cleaning.clean_record(_raw_posting(), city_name_to_id={"Hyderabad": 1})
    assert set(result["preferred_skills"]) <= set(result["skills"])
    assert result["preferred_skills"] == ["Python"]


def test_clean_record_applicant_count_qualifier_absent_is_none():
    # No qualifier supplied -- must come back as None, not a missing key
    # or a copy of the sentinel.
    result = cleaning.clean_record(_raw_posting(), city_name_to_id={"Hyderabad": 1})
    assert result["posting"]["applicant_count_qualifier"] is None
    assert result["posting"]["applicant_count"] == 44


def test_clean_record_applicant_count_qualifier_preserved():
    raw = _raw_posting(applicant_count=100, applicant_count_qualifier="at_least")
    result = cleaning.clean_record(raw, city_name_to_id={"Hyderabad": 1})
    assert result["posting"]["applicant_count_qualifier"] == "at_least"


def test_clean_record_handles_missing_optional_fields():
    # A minimal raw record with almost nothing found -- clean_record
    # must not raise, and everything absent should resolve to None/[].
    minimal = {"title": "not found", "company": "not found"}
    result = cleaning.clean_record(minimal, city_name_to_id={})
    assert result["posting"]["title"] is None
    assert result["posting"]["city_ids"] == []
    assert result["skills"] == []
    assert result["preferred_skills"] == []


# =====================================================================
# SOURCE — which job board a posting came from
# =====================================================================
def test_source_recognises_naukri_and_hirist():
    assert cleaning.source_from_url(
        "https://www.naukri.com/job-listings-data-engineer-acme-hyderabad-123") == "naukri"
    assert cleaning.source_from_url(
        "https://www.hirist.tech/j/data-engineer-python-1667157") == "hirist"


def test_source_treats_hirist_com_and_tech_as_one_board():
    # Info Edge serves the same postings on both domains -- splitting them
    # would put one board in two buckets and make every per-source figure
    # depend on which spelling the search happened to return.
    assert cleaning.source_from_url(
        "https://www.hirist.com/j/frontend-developer-1439211.html") == "hirist"


def test_source_unknown_host_is_other_not_guessed():
    # The whole point of 'other': folding an unrecognised host into the
    # dominant board would invent a fact, the same failure as the old
    # working_type fallback that wrote "On-site" onto 372 rows.
    assert cleaning.source_from_url("https://www.linkedin.com/jobs/view/12345") == "other"
    assert cleaning.source_from_url("https://example.com/careers/1") == "other"


def test_source_missing_url_is_other_never_none():
    # source is NOT NULL on cleaned_postings, so this must never return None.
    assert cleaning.source_from_url(None) == "other"
    assert cleaning.source_from_url("") == "other"


def test_source_is_case_insensitive():
    assert cleaning.source_from_url("HTTPS://WWW.NAUKRI.COM/JOB-123") == "naukri"


def test_clean_record_carries_source():
    result = cleaning.clean_record(
        _raw_posting(url="https://www.hirist.tech/j/data-engineer-1667157"),
        city_name_to_id={"Hyderabad": 1})
    assert result["posting"]["source"] == "hirist"


# =====================================================================
# LOCATION — slash-joined fragments
# =====================================================================
_CITY_IDS = {"Gurugram": 1, "Delhi / NCR": 2, "Delhi": 3,
             "Hyderabad": 4, "Bengaluru": 5, "Mumbai": 6}


def test_slash_joined_renamed_city_resolves_to_one_city():
    # Naukri writes Gurugram as "Gurgaon/Gurugram". Both halves are aliased
    # to the same canonical name, so this is one city, not two.
    ids, unmapped = cleaning.resolve_locations("Gurgaon/Gurugram", _CITY_IDS)
    assert ids == [1]
    assert unmapped == []


def test_delhi_ncr_is_not_split_on_its_own_slash():
    # The regression this fix could have caused: "Delhi / NCR" is itself a
    # canonical city name. Splitting eagerly would resolve it to both
    # Delhi / NCR and Delhi.
    ids, unmapped = cleaning.resolve_locations("Delhi / NCR", _CITY_IDS)
    assert ids == [2]
    assert unmapped == []


def test_slash_joined_distinct_cities_resolve_to_both():
    ids, unmapped = cleaning.resolve_locations("Bangalore/Hyderabad", _CITY_IDS)
    assert ids == [4, 5]
    assert unmapped == []


def test_slash_fragment_keeps_the_half_nobody_recognises():
    # Half resolving must not swallow the half that did not -- the posting
    # named a real place and it stays visible for a curated entry.
    ids, unmapped = cleaning.resolve_locations("Mumbai/Nashik", _CITY_IDS)
    assert ids == [6]
    assert unmapped == ["nashik"]


def test_wholly_unmappable_slash_fragment_stays_one_fragment():
    # Not a city at all. Splitting it would turn one unmappable thing into
    # two, which reads as two pending locations needing curation.
    ids, unmapped = cleaning.resolve_locations(
        "Anywhere in India/Multiple Locations", _CITY_IDS)
    assert ids == []
    assert unmapped == ["Anywhere in India/Multiple Locations"]


def test_parenthetical_locality_still_resolves():
    ids, unmapped = cleaning.resolve_locations("Hyderabad( Raidurgam )", _CITY_IDS)
    assert ids == [4]
    assert unmapped == []


# =====================================================================
# LOCATION — a parenthetical locality containing a comma
# =====================================================================
def test_comma_inside_a_parenthetical_does_not_split_the_city_off():
    # Naukri writes several localities as "Hyderabad( Gachibowli, HITEC City )".
    # Splitting on commas first tore this into "Hyderabad( Gachibowli" and
    # "HITEC City )" -- two fragments matching nothing -- and the posting was
    # left with NO city, invisible to every location filter.
    ids, unmapped = cleaning.resolve_locations(
        "Hyderabad( Gachibowli, HITEC City )", _CITY_IDS)
    assert ids == [4]
    assert unmapped == []


def test_parenthetical_stripped_across_several_cities():
    ids, unmapped = cleaning.resolve_locations(
        "Bengaluru, Hyderabad( Gachibowli, HITEC City ), Mumbai", _CITY_IDS)
    assert ids == [4, 5, 6]
    assert unmapped == []


def test_bracketed_spelling_still_resolves_after_the_strip():
    # "Mumbai (All Areas)" used to match a literal alias. Now the bracket is
    # removed first and it resolves as plain Mumbai -- same answer, and the
    # alias is no longer load-bearing.
    ids, _ = cleaning.resolve_locations("Mumbai (All Areas)", _CITY_IDS)
    assert ids == [6]


def test_delhi_ncr_without_a_separator_resolves():
    # "delhi / ncr", "delhi/ncr" and "ncr" were all mapped; the plain-space
    # spelling Naukri also writes was not, leaving 3 postings unmapped.
    for spelling in ("Delhi NCR", "delhi ncr", "Delhi-NCR", "Delhi / NCR"):
        ids, unmapped = cleaning.resolve_locations(spelling, _CITY_IDS)
        assert ids == [2], f"{spelling!r} did not resolve"
        assert unmapped == []
