"""Unit tests for the regex skill/certification vocabulary. No database."""

import skill_taxonomy as st


def test_extract_skills_finds_known_tools():
    found = st.extract_skills("Looking for someone strong in Python, AWS and Docker.")
    assert "Python" in found
    assert "AWS" in found
    assert "Docker" in found


def test_extract_skills_empty_description():
    assert st.extract_skills("") == []
    assert st.extract_skills(None) == []


def test_extract_skills_no_duplicates():
    found = st.extract_skills("Python developer needed. Must know Python well. Python, Python.")
    assert found.count("Python") == 1


def test_extract_certifications_finds_known_credential():
    found = st.extract_certifications("Candidate should be PMP certified and know Six Sigma.")
    assert "PMP" in found
    assert "Six Sigma" in found


def test_extract_certifications_empty_description():
    assert st.extract_certifications("") == []
    assert st.extract_certifications(None) == []


def test_extract_certifications_no_false_positive_on_unrelated_text():
    assert st.extract_certifications("We need a great communicator with 5 years experience.") == []
