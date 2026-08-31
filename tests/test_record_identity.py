"""Guards against a whole scrape run collapsing onto one database row.

The fingerprint hashes company + title + location + experience. When all four
are empty every record hashes identically, so ON CONFLICT folds the batch into
a single row -- measured at 40 distinct postings in, 1 row out, with the run
still reporting storage_ok. That is reachable in practice: scrape_job_detail
keeps a posting whenever the DESCRIPTION selector renders, while title and
company come from safe_text(), which returns None rather than raising. A
Naukri markup change touching one selector and not the other produces exactly
this input.

These tests are about the shape of a record, so they need no database.
"""

import cleaning
import job_database


LOOKUP = {"Hyderabad": 1}


def _record(**over):
    base = {"url": "https://www.naukri.com/job-listings-x-1", "title": "Backend Developer",
            "company": "Acme", "location": "Hyderabad", "experience": "3-5 Yrs",
            "description": "Real description text"}
    base.update(over)
    return base


# ---------------------------------------------------------------------
# The collapse itself
# ---------------------------------------------------------------------
def test_records_with_no_title_or_company_share_one_fingerprint():
    """The property that makes the collapse possible. Not a bug on its own --
    it is why the guard below has to exist."""
    blank = [_record(title=None, company=None, url=f"https://x/{i}") for i in range(5)]
    fps = {cleaning.clean_record(r, LOOKUP, set())["posting"]["fingerprint"] for r in blank}
    assert len(fps) == 1


def test_a_posting_with_neither_title_nor_company_is_not_identifiable():
    assert job_database.is_identifiable(
        cleaning.clean_record(_record(title=None, company=None), LOOKUP, set())["posting"]
    ) is False


def test_empty_strings_and_whitespace_count_as_missing():
    for blank in ("", "   ", "\t\n"):
        posting = cleaning.clean_record(
            _record(title=blank, company=blank), LOOKUP, set())["posting"]
        assert job_database.is_identifiable(posting) is False, repr(blank)


# ---------------------------------------------------------------------
# ...but only that case. A real posting must never be discarded.
# ---------------------------------------------------------------------
def test_a_title_alone_is_enough():
    posting = cleaning.clean_record(_record(company=None), LOOKUP, set())["posting"]
    assert job_database.is_identifiable(posting) is True


def test_a_company_alone_is_enough():
    posting = cleaning.clean_record(_record(title=None), LOOKUP, set())["posting"]
    assert job_database.is_identifiable(posting) is True


def test_an_ordinary_posting_is_identifiable():
    posting = cleaning.clean_record(_record(), LOOKUP, set())["posting"]
    assert job_database.is_identifiable(posting) is True


def test_missing_location_and_experience_do_not_disqualify():
    """Naukri omits these often enough that treating them as required would
    throw away real postings -- the point is identity, not completeness."""
    posting = cleaning.clean_record(
        _record(location=None, experience=None), LOOKUP, set())["posting"]
    assert job_database.is_identifiable(posting) is True


# ---------------------------------------------------------------------
# NUL bytes: cleaning let them through, and psycopg2 rejects them at
# INSERT time, which fails the whole batch rather than one record.
# ---------------------------------------------------------------------
def test_nul_bytes_are_stripped_during_cleaning():
    posting = cleaning.clean_record(
        _record(title="Python Dev\x00", company="Ac\x00me"), LOOKUP, set())["posting"]
    assert "\x00" not in posting["title"]
    assert "\x00" not in posting["company"]
    assert posting["title"] == "Python Dev"


def test_stripping_a_nul_does_not_empty_an_otherwise_real_value():
    posting = cleaning.clean_record(_record(title="\x00"), LOOKUP, set())["posting"]
    # All that was there was the NUL, so this is an absence, not a value.
    assert posting["title"] is None
