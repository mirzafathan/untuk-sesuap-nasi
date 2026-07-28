import pytest

from job_filter import TARGET_JOB_KEYWORDS, matches_target_role


@pytest.mark.parametrize("keyword", TARGET_JOB_KEYWORDS)
def test_matches_each_target_phrase_case_insensitively(keyword: str) -> None:
    assert matches_target_role(f"Senior {keyword.upper()} II")


def test_matching_accepts_common_full_stack_and_machine_learning_variants() -> None:
    assert matches_target_role("Software Engineering Manager")
    assert matches_target_role("Machine Learning Engineer")
    assert matches_target_role("Full Stack Engineer")
    assert matches_target_role("Senior Full-Stack Engineer")


@pytest.mark.parametrize(
    "title",
    [
        "Back End Engineer",
        "Back-End Engineer",
        "Artificial Intelligence Engineer",
        "Large Language Model Engineer",
    ],
)
def test_matching_accepts_unambiguous_long_form_variants(title: str) -> None:
    assert matches_target_role(title)


def test_unrelated_title_does_not_match() -> None:
    assert not matches_target_role("Product Manager")
    assert not matches_target_role("Machine Learning Scientist")
    assert not matches_target_role("Full Stack Developer")
