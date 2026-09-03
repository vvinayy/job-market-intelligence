"""Unit tests for hirist_collector's pure mapping. No network, no browser,
no database — these run in milliseconds.

The load-bearing test is the last one: the collector's whole reason for
existing in this shape is that clean_record() consumes its output unchanged.
If that stops being true, a second source has quietly become a second
pipeline.
"""

import cleaning
import hirist_collector as hc


# A realistic payload, trimmed to the keys the mapper reads. Values are the
# shapes the live API actually returns, including the nested companyData that
# a flat key scan misses.
PAYLOAD = {
    "title": "Backend Engineer",
    "companyData": {"companyId": 0, "companyName": "Acme Data Systems"},
    "jobDetailUrl": "https://www.hirist.tech/j/backend-engineer-1234567",
    "min": 3, "max": 10,
    "locations": [{"id": 2, "name": "Hyderabad"}],
    "tags": [{"id": 1, "name": "Python", "isMandatory": True},
             {"id": 2, "name": "Django", "isMandatory": False}],
    "introText": "<p>Build services in <b>Python</b>.</p><p>Hyderabad based.</p>",
    "createdTime": 1786645800000,
    "applyCount": 55,
    "minRatingAb": 3.8,
    "hideSal": 1, "minSal": 0, "maxSal": 0,
    "workFromHome": 0,
}


# ---------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------
def test_strip_html_removes_markup_and_keeps_the_words():
    out = hc.strip_html("<p>Build services in <b>Python</b>.</p>")
    assert "<" not in out and ">" not in out
    assert "Python" in out


def test_strip_html_turns_block_tags_into_line_breaks():
    # split_description_sections() looks for headings on their own lines, and
    # find_skill_choice_groups() reads sentence punctuation. Collapsing block
    # tags to spaces would destroy both.
    out = hc.strip_html("<p>Responsibilities</p><p>Write code</p>")
    assert "\n" in out


def test_strip_html_unescapes_entities():
    assert "&amp;" not in hc.strip_html("<p>R&amp;D team</p>")


def test_strip_html_none_input():
    assert hc.strip_html(None) is None
    assert hc.strip_html("") is None


# ---------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------
def test_experience_is_rendered_into_the_form_cleaning_already_parses():
    text = hc._experience_text({"min": 3, "max": 10})
    assert text == "3 - 10 years"
    # The point of the string form: one parser serves both sources.
    assert cleaning.parse_range_min(text) == 3
    assert cleaning.parse_range_max(text) == 10


def test_experience_absent_is_none_not_zero():
    assert hc._experience_text({}) is None


def test_salary_hidden_returns_nothing_rather_than_zero():
    # hideSal is set on most postings, with minSal/maxSal left at 0. Reporting
    # 0 would state a salary nobody offered.
    assert hc._salary_text({"hideSal": 1, "minSal": 0, "maxSal": 0}) is None


def test_salary_disclosed_is_parseable():
    text = hc._salary_text({"hideSal": 0, "minSal": 20, "maxSal": 30})
    assert cleaning.parse_range_min(text) == 20
    assert cleaning.parse_range_max(text) == 30


def test_working_type_absent_is_none_not_onsite():
    # The 372-row lesson: silence means "not stated", never "office".
    assert hc._working_type({"workFromHome": 0}) is None
    assert hc._working_type({}) is None


def test_working_type_reads_a_real_flag():
    assert hc._working_type({"workFromHome": 1}) == "Remote"


def test_posted_converts_epoch_milliseconds():
    day, raw = hc._posted({"createdTime": 1786645800000})
    assert day.isoformat() == raw
    assert day.year == 2026


def test_posted_missing_is_none():
    assert hc._posted({}) == (None, None)


# ---------------------------------------------------------------------
# build_record
# ---------------------------------------------------------------------
def test_build_record_reads_company_from_the_nested_object():
    # companyName lives inside companyData; a flat scan of the payload's top
    # level finds nothing and would wrongly conclude the field is absent.
    assert hc.build_record(PAYLOAD)["company"] == "Acme Data Systems"


def test_build_record_keeps_every_tag_as_a_skill():
    # Mandatory and optional tags alike. Dropping the optional ones would
    # leave hirist postings carrying ~3 skills against Naukri's ~13, and no
    # cross-source skill figure would mean anything.
    assert hc.build_record(PAYLOAD)["key_skills"] == ["Python", "Django"]


def test_build_record_does_not_report_mandatory_as_preferred():
    # preferred_skill_ids records Naukri's starred chips -- an employer's
    # "preferred" marker. hirist publishes "mandatory", a different statement,
    # and how much of a posting it covers varies by employer. Mapping one onto
    # the other would put two different facts in one column.
    assert "preferred_key_skills" not in hc.build_record(PAYLOAD)


def test_no_preferred_skill_survives_the_whole_hirist_chain():
    """The unit test above pins the collector's dict. This pins the result.

    The collector being right was never the weak point -- 19 rows written
    before it was fixed sat in the database contradicting it, telling readers
    a mandatory skill was preferred. This asserts the end state a scrape
    actually produces, so a default reintroduced anywhere between the payload
    and the row fails here rather than a day later in the data.
    """
    import cleaning
    cleaned = cleaning.clean_record(hc.build_record(PAYLOAD),
                                    city_name_to_id={"Hyderabad": 1})
    assert cleaned["preferred_skills"] == []
    # and the mandatory skills are not lost -- they are ordinary requirements
    assert "Python" in cleaned["skills"]


def test_build_record_marks_absent_fields_not_found():
    record = hc.build_record(PAYLOAD)
    assert record["salary"] == hc.NOT_FOUND
    assert record["working_type"] == hc.NOT_FOUND


def test_build_record_survives_an_empty_payload():
    # One malformed posting must cost one posting, not the run.
    record = hc.build_record({})
    assert record["title"] == hc.NOT_FOUND
    assert record["key_skills"] == []


# ---------------------------------------------------------------------
# The contract: cleaning.py consumes this unchanged
# ---------------------------------------------------------------------
def test_clean_record_accepts_a_hirist_record_unchanged():
    record = hc.build_record(PAYLOAD, "https://www.hirist.tech/search/x")
    cleaned = cleaning.clean_record(record, {"Hyderabad": 2}, set())
    posting = cleaned["posting"]

    assert posting["company"] == "Acme Data Systems"
    assert posting["experience_min"] == 3 and posting["experience_max"] == 10
    assert posting["city_ids"] == [2]
    assert posting["fingerprint"]
    # Not fabricated: hirist publishes neither, so both stay NULL.
    assert posting["salary_min"] is None
    assert posting["working_type"] is None
    # Derived layers still work, because they read title and description.
    assert posting["role_family"]
    assert "Python" in cleaned["skills"]
