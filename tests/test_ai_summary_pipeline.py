import json
from pathlib import Path

import httpx

from main import ScraperTarget, run
from models import JobDetails, JobPosting
from notifier import TelegramNotifier
from state_manager import StateManager
from summarizer import OpenRouterSummarizer


class DetailScraper:
    def __init__(self, posting: JobPosting) -> None:
        self.posting = posting
        self.detail_calls = 0
        self.closed = False

    def extract_jobs(self, url: str) -> list[JobPosting]:
        return [self.posting]

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        self.detail_calls += 1
        return JobDetails(
            description_text=(
                "Design Python APIs and operate PostgreSQL services. "
                "Candidates need three years of backend experience."
            ),
            location="Jakarta",
            work_arrangement="Hybrid",
            employment_type="Full-time",
        )

    def close(self) -> None:
        self.closed = True


def test_new_job_flows_from_mocked_openrouter_to_telegram_and_state(
    tmp_path: Path,
) -> None:
    posting = JobPosting(
        id="job-1",
        title="Backend Engineer",
        company="Example",
        url="https://example.test/jobs/1",
    )
    scraper = DetailScraper(posting)
    openrouter_requests: list[httpx.Request] = []
    telegram_messages: list[str] = []

    def openrouter_handler(request: httpx.Request) -> httpx.Response:
        openrouter_requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "overview": "Build reliable backend services.",
                                    "responsibilities": ["Design Python APIs"],
                                    "requirements": ["Three years of experience"],
                                    "tech_stack": ["Python", "PostgreSQL"],
                                    "experience": "3 years",
                                    "location": "Jakarta",
                                    "work_arrangement": "Hybrid",
                                    "employment_type": "Full-time",
                                    "salary": None,
                                }
                            )
                        }
                    }
                ]
            },
        )

    def telegram_handler(request: httpx.Request) -> httpx.Response:
        telegram_messages.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    openrouter_client = httpx.Client(transport=httpx.MockTransport(openrouter_handler))
    telegram_client = httpx.Client(transport=httpx.MockTransport(telegram_handler))
    summarizer = OpenRouterSummarizer(
        api_key="fake-key",
        client=openrouter_client,
        retry_backoff_seconds=0,
    )
    notifier = TelegramNotifier(
        token="fake-token",
        chat_id="fake-chat",
        client=telegram_client,
    )
    state = StateManager(tmp_path / "seen.json")

    try:
        result = run(
            targets=[
                ScraperTarget(
                    name="Example",
                    url="https://example.test/careers",
                    factory=lambda: scraper,
                )
            ],
            state_manager=state,
            notifier=notifier,
            summarizer=summarizer,
        )
    finally:
        openrouter_client.close()
        telegram_client.close()

    assert result == [posting]
    assert scraper.detail_calls == 1
    assert scraper.closed is True
    assert len(openrouter_requests) == 1
    assert len(telegram_messages) == 1
    assert "Build reliable backend services\\." in telegram_messages[0]
    assert "Design Python APIs" in telegram_messages[0]
    assert "Three years of experience" in telegram_messages[0]
    assert state.get_new_jobs([posting]) == []
