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


# ---------------------------------------------------------------------
# Skill choice groups — which skills a posting offers as alternatives.
# Every case below is a real sentence shape taken from scraped postings;
# the mixed AND/OR one defeated three earlier regex attempts.
# ---------------------------------------------------------------------
def test_choice_groups_simple_or_list():
    groups = st.find_skill_choice_groups(
        "Experience with cloud platforms such as AWS, Azure, or GCP.",
        ["AWS", "Azure", "GCP"])
    assert groups == [("AWS", "Azure", "GCP")]


def test_choice_groups_separates_required_from_choices():
    # Git, Docker and CI/CD are required; the two "or"s are separate
    # choices. Grouping them together, or sweeping the required ones in,
    # are both wrong.
    groups = st.find_skill_choice_groups(
        "Experience with Git, Maven or Gradle, Docker, CI/CD, and AWS, Azure, or GCP.",
        ["Git", "Maven", "Gradle", "Docker", "CI/CD", "AWS", "Azure", "GCP"])
    assert groups == [("Maven", "Gradle"), ("AWS", "Azure", "GCP")]


def test_choice_groups_and_is_not_a_choice():
    assert st.find_skill_choice_groups(
        "Hands-on experience with AWS and Azure services.", ["AWS", "Azure"]) == []


def test_choice_groups_or_binds_to_nearest_terms():
    # The "or" belongs to Nginx/Apache — React and Vue are both used.
    groups = st.find_skill_choice_groups(
        "Deployment of web servers (Django) & Vue JS, React JS using Nginx or Apache.",
        ["Django", "Vue", "React", "Nginx", "Apache"])
    assert groups == [("Nginx", "Apache")]


def test_choice_groups_does_not_run_past_the_list():
    # "...or similar tools FOR Java/Node/Python" — Java is not a third
    # build tool, and the group must survive the unresolvable tail.
    groups = st.find_skill_choice_groups(
        "Experience with Maven, Gradle, or similar tools for Java/Node/Python environments.",
        ["Maven", "Gradle", "Java", "Node.js", "Python"])
    assert groups == [("Maven", "Gradle")]


def test_choice_groups_keeps_single_letter_languages():
    # "R" is a real language; a length filter would silently drop it.
    groups = st.find_skill_choice_groups(
        "Proficiency in Python or R for data processing.",
        ["Python", "R", "Data Processing"])
    assert groups == [("Python", "R")]


def test_choice_groups_respects_blocklist():
    groups = st.find_skill_choice_groups(
        "Experience with AWS or cloud services.",
        ["AWS", "Cloud Services"], blocklist={"Cloud Services"})
    assert groups == []


def test_choice_groups_never_invents_a_skill():
    # Only names passed in may come back out.
    given = ["AWS", "Azure"]
    for group in st.find_skill_choice_groups(
            "Experience with AWS, Azure, or Oracle Cloud.", given):
        assert set(group) <= set(given)


def test_choice_groups_empty_inputs():
    assert st.find_skill_choice_groups("", ["AWS", "Azure"]) == []
    assert st.find_skill_choice_groups(None, ["AWS", "Azure"]) == []
    assert st.find_skill_choice_groups("AWS or Azure.", ["AWS"]) == []
