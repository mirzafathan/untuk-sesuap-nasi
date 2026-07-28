import hashlib

import pytest
from pydantic import ValidationError

from models import JobAlert, JobDetails, JobPosting, JobSummary


def test_job_posting_defaults_posted_date_to_none() -> None:
    job = JobPosting(
        id="raw-123",
        title="Backend Engineer",
        company="Example Co",
        url="https://example.com/jobs/123",
    )

    assert job.posted_date is None


def test_composite_id_hashes_exact_company_title_and_url() -> None:
    job = JobPosting(
        id="raw-id-is-not-used",
        title="Senior Backend Engineer",
        company="Example Co",
        url="https://example.com/jobs/backend?ref=career",
    )
    expected = hashlib.sha256(
        b"Example Co | Senior Backend Engineer | https://example.com/jobs/backend?ref=career"
    ).hexdigest()

    assert job.composite_id() == expected


def test_composite_id_does_not_normalize_exact_values() -> None:
    first = JobPosting(id="1", title="AI Engineer", company="Acme", url="https://a.test/1")
    second = JobPosting(id="1", title="ai engineer", company="Acme", url="https://a.test/1")

    assert first.composite_id() != second.composite_id()


def test_job_details_require_nonblank_cleaned_description() -> None:
    with pytest.raises(ValidationError):
        JobDetails(description_text="   ")

    details = JobDetails(
        description_text="  Build reliable services.  ",
        location=" Jakarta ",
    )

    assert details.description_text == "Build reliable services."
    assert details.location == "Jakarta"


def test_job_details_normalize_blank_optional_metadata_to_none() -> None:
    details = JobDetails(
        description_text="Build reliable services.",
        location="   ",
        work_arrangement="",
        employment_type=None,
        salary="  ",
    )

    assert details.location is None
    assert details.work_arrangement is None
    assert details.employment_type is None
    assert details.salary is None


def test_job_summary_enforces_compact_telegram_limits() -> None:
    summary = JobSummary(
        overview="Build reliable payment services.",
        responsibilities=["Design APIs", "Operate services"],
        requirements=["Five years of experience"],
        tech_stack=["Python", "PostgreSQL"],
    )

    assert summary.tech_stack == ["Python", "PostgreSQL"]

    with pytest.raises(ValidationError):
        JobSummary(
            overview="Too many responsibilities",
            responsibilities=["one", "two", "three", "four"],
        )


def test_summary_does_not_change_posting_identity() -> None:
    posting = JobPosting(
        id="1",
        title="Backend Engineer",
        company="Acme",
        url="https://example.test/jobs/1",
    )
    before = posting.composite_id()
    alert = JobAlert(
        posting=posting,
        details=JobDetails(description_text="Build APIs"),
        summary=JobSummary(overview="Build APIs for Acme."),
    )

    assert alert.posting.composite_id() == before
