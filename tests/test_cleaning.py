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


def test_description_hash_none_for_empty():
    assert cleaning.make_description_hash(None) is None
    assert cleaning.make_description_hash("") is None


def test_description_hash_deterministic():
    a = cleaning.make_description_hash("We are looking for a Python developer.")
    b = cleaning.make_description_hash("We are looking for a Python developer.")
    assert a == b
    assert a is not None


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
    assert cleaning.parse_employment_type("Full Time, Permanent") == "Full Time"
    assert cleaning.parse_contract_type("Full Time, Permanent") == "Permanent"


def test_employment_type_no_match_is_none():
    assert cleaning.parse_employment_type("Something Unexpected") is None


def test_working_type_no_badge_is_onsite():
    # Naukri only badges Hybrid/Remote/WFH postings -- no badge means
    # On-site by convention, not "unknown".
    assert cleaning.normalize_working_type(None) == "On-site"
    assert cleaning.normalize_working_type("Work from office") == "On-site"


def test_working_type_hybrid_and_remote():
    assert cleaning.normalize_working_type("Hybrid work mode") == "Hybrid"
    assert cleaning.normalize_working_type("Work from home") == "Remote"


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
    assert posting["employment_type"] == "Full Time"
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
