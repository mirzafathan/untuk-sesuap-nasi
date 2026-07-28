import os

import pytest
from dotenv import load_dotenv

from models import JobDetails, JobPosting
from summarizer import OpenRouterSummarizer


@pytest.mark.integration
def test_live_openrouter_summary_when_explicitly_enabled() -> None:
    load_dotenv(override=False)
    if os.getenv("RUN_OPENROUTER_INTEGRATION") != "1":
        pytest.skip("set RUN_OPENROUTER_INTEGRATION=1 to run the billable live smoke test")
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY is not configured")

    summarizer = OpenRouterSummarizer()
    try:
        summary = summarizer.summarize(
            JobPosting(
                id="integration-test",
                title="Backend Engineer",
                company="Example",
                url="https://example.test/jobs/integration-test",
            ),
            JobDetails(
                description_text=(
                    "Build Python APIs backed by PostgreSQL. Operate services in "
                    "production and improve reliability. Candidates need three years "
                    "of backend engineering experience. This is a full-time hybrid role "
                    "in Jakarta."
                ),
                location="Jakarta",
                work_arrangement="Hybrid",
                employment_type="Full-time",
            ),
        )
    finally:
        summarizer.close()

    assert summary.overview
    assert len(summary.responsibilities) <= 3
    assert len(summary.requirements) <= 3
    assert "Python" in summary.tech_stack
