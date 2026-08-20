"""Unit tests for cleaning.py's education-degree parser, checked against
every distinct real field_of_study pattern found in this project's own
data (pulled directly from posting_qualifications before this feature
was built). This is the riskiest piece of new logic in the education
reference-table change: the source text uses a comma for two different
things (a new degree, or another specialization for the same degree)
with no punctuation telling them apart, so each case here is a real
example, not an invented one."""

import cleaning


def _by_degree(result: list[dict]) -> dict[str, dict]:
    return {r["degree"]: r for r in result}


def test_empty_and_none_input():
    assert cleaning._parse_field_of_study(None) == []
    assert cleaning._parse_field_of_study("") == []


def test_standalone_any_graduate():
    result = cleaning._parse_field_of_study("Any Graduate")
    assert result == [{"degree": "Any Graduate", "level": "UG", "specializations": []}]


def test_slash_degree_splits_into_two_alternatives():
    # The user's own example: B.Tech/B.E. must become two separate
    # degree references, not one combined "B.Tech / B.E." entry.
    result = cleaning._parse_field_of_study("B.Tech / B.E. in Any Specialization")
    by_degree = _by_degree(result)
    assert set(by_degree) == {"B.Tech", "B.E."}
    assert by_degree["B.Tech"]["level"] == "UG"
    assert by_degree["B.Tech"]["specializations"] == ["Any Specialization"]
    assert by_degree["B.E."]["specializations"] == ["Any Specialization"]


def test_three_ug_degrees_same_specialization():
    result = cleaning._parse_field_of_study(
        "B.Tech / B.E. in Any Specialization, B.Sc in Any Specialization, "
        "B.C.A. in Any Specialization"
    )
    by_degree = _by_degree(result)
    assert set(by_degree) == {"B.Tech", "B.E.", "B.Sc", "B.C.A."}
    for degree in by_degree.values():
        assert degree["level"] == "UG"
        assert degree["specializations"] == ["Any Specialization"]


def test_comma_separated_specializations_stay_with_the_right_degree():
    # Real row: "B.Sc in Computer Science and Technology, Information
    # Technology (IT), B.A - Bachelor of Arts in Computer Science,
    # B.Tech / B.E. in Computer Science and Engineering (CSE),
    # Information Technology, B.C.A. in Computer Applications (General)"
    # The second "Information Technology" must attach to B.Tech/B.E,
    # not become its own degree or leak onto B.A or B.C.A.
    raw = (
        "B.Sc in Computer Science and Technology, Information Technology (IT), "
        "B.A - Bachelor of Arts in Computer Science, "
        "B.Tech / B.E. in Computer Science and Engineering (CSE), Information Technology, "
        "B.C.A. in Computer Applications (General)"
    )
    result = cleaning._parse_field_of_study(raw)
    by_degree = _by_degree(result)

    assert set(by_degree) == {"B.Sc", "B.A", "B.Tech", "B.E.", "B.C.A."}
    assert by_degree["B.Sc"]["specializations"] == [
        "Computer Science and Technology", "Information Technology",
    ]
    assert by_degree["B.A"]["specializations"] == ["Computer Science"]
    assert by_degree["B.Tech"]["specializations"] == [
        "Computer Science and Engineering (CSE)", "Information Technology",
    ]
    assert by_degree["B.E."]["specializations"] == by_degree["B.Tech"]["specializations"]
    assert by_degree["B.C.A."]["specializations"] == ["Computer Applications (General)"]


def test_information_technology_it_alias_matches_bare_form():
    # "Information Technology (IT)" and "Information Technology" must
    # normalize to the same canonical specialization.
    result = cleaning._parse_field_of_study("B.Sc in Information Technology (IT)")
    assert result[0]["specializations"] == ["Information Technology"]


def test_standalone_entry_after_a_full_degree_is_its_own_group():
    # "Any Graduate" here is a second, independent accepted qualification
    # -- not a specialization tacked onto the B.Tech/B.E. group before it.
    result = cleaning._parse_field_of_study("B.Tech / B.E. in Any Specialization, Any Graduate")
    by_degree = _by_degree(result)
    assert by_degree["Any Graduate"]["specializations"] == []
    assert by_degree["B.Tech"]["specializations"] == ["Any Specialization"]


def test_pg_multiple_degrees_with_extra_specialization():
    # Real row: "M.Tech in Computers, MCA in Computers, Artificial
    # Intelligence and Machine Learning, MS/M.Sc(Science) in Computers"
    raw = (
        "M.Tech in Computers, MCA in Computers, "
        "Artificial Intelligence and Machine Learning, MS/M.Sc(Science) in Computers"
    )
    result = cleaning._parse_field_of_study(raw)
    by_degree = _by_degree(result)

    assert set(by_degree) == {"M.Tech", "MCA", "MS", "M.Sc(Science)"}
    for degree in by_degree.values():
        assert degree["level"] == "PG"
    assert by_degree["M.Tech"]["specializations"] == ["Computers"]
    assert by_degree["MCA"]["specializations"] == ["Computers", "Artificial Intelligence and Machine Learning"]
    assert by_degree["MS"]["specializations"] == ["Computers"]
    assert by_degree["M.Sc(Science)"]["specializations"] == ["Computers"]


def test_mba_pgdm_and_mcm_split_correctly():
    # Real row: "MBA/PGDM in Information Technology, M.Tech in Computers,
    # MCM in Computers and Management, MS/M.Sc(Science) in Computers,
    # MCA in Computers"
    raw = (
        "MBA/PGDM in Information Technology, M.Tech in Computers, "
        "MCM in Computers and Management, MS/M.Sc(Science) in Computers, MCA in Computers"
    )
    result = cleaning._parse_field_of_study(raw)
    by_degree = _by_degree(result)

    assert set(by_degree) == {"MBA", "PGDM", "M.Tech", "MCM", "MS", "M.Sc(Science)", "MCA"}
    assert by_degree["MBA"]["specializations"] == ["Information Technology"]
    assert by_degree["PGDM"]["specializations"] == ["Information Technology"]
    assert by_degree["MCM"]["specializations"] == ["Computers and Management"]


def test_doctorate_slash_degree_and_standalones():
    assert cleaning._parse_field_of_study("Any Doctorate") == [
        {"degree": "Any Doctorate", "level": "Doctorate", "specializations": []}
    ]
    assert cleaning._parse_field_of_study("Doctorate Not Required") == [
        {"degree": "Doctorate Not Required", "level": "Doctorate", "specializations": []}
    ]
    result = cleaning._parse_field_of_study("Ph.D/Doctorate in Any Specialization")
    by_degree = _by_degree(result)
    assert set(by_degree) == {"Ph.D", "Doctorate"}
    assert by_degree["Ph.D"]["specializations"] == ["Any Specialization"]


def test_unrecognized_token_with_no_fallback_level_is_dropped():
    # A direct call with no block context (no fallback_level) and
    # nothing before it to attach to as a specialization -- there's
    # nothing safe to do with it, so it's dropped, same as before.
    assert cleaning._parse_field_of_study("Something Naukri Has Never Shown Before") == []


def test_unrecognized_leading_token_registers_as_new_degree_with_fallback_level():
    # With a fallback_level supplied (the real production path, via
    # parse_education_degrees passing Naukri's own UG/PG/Doctorate
    # label), an unfamiliar degree name is preserved instead of dropped
    # -- same "fall through to itself" rule as an unrecognized skill.
    result = cleaning._parse_field_of_study("B.Pharm in Any Specialization", fallback_level="UG")
    assert result == [{"degree": "B.Pharm", "level": "UG", "specializations": ["Any Specialization"]}]


def test_unrecognized_token_with_fallback_level_and_no_specialization():
    result = cleaning._parse_field_of_study("CA", fallback_level="PG")
    assert result == [{"degree": "CA", "level": "PG", "specializations": []}]


def test_unrecognized_slash_token_still_splits_into_alternatives():
    # The slash-splitting rule applies even to a degree name this
    # taxonomy has never seen before.
    result = cleaning._parse_field_of_study("CA/ICWA in Any Specialization", fallback_level="PG")
    by_degree = _by_degree(result)
    assert set(by_degree) == {"CA", "ICWA"}
    assert by_degree["CA"]["specializations"] == ["Any Specialization"]


def test_unrecognized_token_after_a_known_group_is_still_a_specialization_not_a_new_degree():
    # Order matters: an unfamiliar word right after a KNOWN degree is
    # still treated as a continuation specialization for that degree,
    # not registered as its own new degree -- the fallback path only
    # fires when there's nothing before it to attach to.
    result = cleaning._parse_field_of_study(
        "B.Tech / B.E. in Robotics and Automation Engineering", fallback_level="UG"
    )
    by_degree = _by_degree(result)
    assert by_degree["B.Tech"]["specializations"] == ["Robotics and Automation Engineering"]


def test_parse_education_degrees_passes_level_through_for_unrecognized_degrees():
    # education dict keys are Naukri's own UG/PG/Doctorate labels --
    # confirms that context correctly reaches an unrecognized degree
    # name, not just the known-vocabulary path.
    education = {"UG": "B.Pharm in Any Specialization", "Doctorate": "MBBS"}
    result = cleaning.parse_education_degrees(education)
    by_degree = _by_degree(result)
    assert by_degree["B.Pharm"]["level"] == "UG"
    assert by_degree["MBBS"]["level"] == "Doctorate"


def test_parse_education_degrees_combines_all_levels():
    education = {"UG": "Any Graduate", "PG": "Any Postgraduate"}
    result = cleaning.parse_education_degrees(education)
    names = {r["degree"] for r in result}
    assert names == {"Any Graduate", "Any Postgraduate"}


def test_parse_education_degrees_non_dict_input():
    assert cleaning.parse_education_degrees(None) == []
    assert cleaning.parse_education_degrees("not a dict") == []


# ---------------------------------------------------------------------
# Vocabulary added from Naukri's own site-wide Education filter panel,
# cross-checked for IT relevance rather than pulled from our own sample.
# ---------------------------------------------------------------------
def test_ca_is_first_class_pg_level():
    # Confirmed via a live Naukri posting during this project's own
    # research ("PG: CA in CA") -- now a named vocabulary entry instead
    # of relying on the auto-registration fallback to catch it.
    result = cleaning._parse_field_of_study("CA in CA")
    assert result == [{"degree": "CA", "level": "PG", "specializations": ["CA"]}]


def test_post_graduation_not_required_is_distinct_from_ug_version():
    assert cleaning._parse_field_of_study("Post Graduation Not Required") == [
        {"degree": "Post Graduation Not Required", "level": "PG", "specializations": []}
    ]
    assert cleaning._parse_field_of_study("Graduation Not Required") == [
        {"degree": "Graduation Not Required", "level": "UG", "specializations": []}
    ]


def test_pg_diploma():
    result = cleaning._parse_field_of_study("PG Diploma in Any Specialization")
    assert result == [{"degree": "PG Diploma", "level": "PG", "specializations": ["Any Specialization"]}]


def test_bba_bms_splits_into_two_alternatives():
    result = cleaning._parse_field_of_study("B.B.A. / B.M.S. in Any Specialization")
    by_degree = _by_degree(result)
    assert set(by_degree) == {"B.B.A.", "B.M.S."}
    for degree in by_degree.values():
        assert degree["level"] == "UG"
        assert degree["specializations"] == ["Any Specialization"]


def test_commerce_degrees():
    assert cleaning._parse_field_of_study("B.Com in Any Specialization")[0]["level"] == "UG"
    assert cleaning._parse_field_of_study("M.Com in Any Specialization")[0]["level"] == "PG"


def test_ma_does_not_collide_with_mba():
    # "M.A" and "MBA/PGDM" share a leading "M" but must never cross-match.
    ma_result = cleaning._parse_field_of_study("M.A in Any Specialization")
    assert ma_result == [{"degree": "M.A", "level": "PG", "specializations": ["Any Specialization"]}]

    mba_result = cleaning._parse_field_of_study("MBA/PGDM in Any Specialization")
    by_degree = _by_degree(mba_result)
    assert set(by_degree) == {"MBA", "PGDM"}


# ---------------------------------------------------------------------
# Non-tech UG degrees, added deliberately broad (not IT-filtered) so a
# new combo registers cleanly without a code change. Exact text
# confirmed against real live postings, not typed in from a screenshot.
# ---------------------------------------------------------------------
def test_llb_canonicalizes_to_short_form():
    # "LLB - Bachelor of Laws" -> "LLB", same drop-the-expansion rule
    # as "B.A - Bachelor of Arts" -> "B.A".
    result = cleaning._parse_field_of_study("LLB - Bachelor of Laws in Any Specialization")
    assert result == [{"degree": "LLB", "level": "UG", "specializations": ["Any Specialization"]}]


def test_bed_and_mbbs():
    assert cleaning._parse_field_of_study("B.Ed in Education") == [
        {"degree": "B.Ed", "level": "UG", "specializations": ["Education"]}
    ]
    assert cleaning._parse_field_of_study("M.B.B.S. in Any Specialization") == [
        {"degree": "M.B.B.S.", "level": "UG", "specializations": ["Any Specialization"]}
    ]
