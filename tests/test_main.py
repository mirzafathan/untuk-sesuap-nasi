from pathlib import Path

import pytest

import main as main_module

from exceptions import DOMStructureChangedError
from main import TARGETS, ScraperTarget, run
from models import JobAlert, JobDetails, JobPosting, JobSummary
from notifier import NotificationError
from state_manager import StateManager
from exceptions import JobDetailStructureChangedError, SummarizationError


def posting(raw_id: str = "1") -> JobPosting:
    return JobPosting(
        id=raw_id,
        title="Backend Engineer",
        company="Acme",
        url=f"https://example.test/jobs/{raw_id}",
    )


class FakeScraper:
    def __init__(
        self,
        result=None,
        error: Exception | None = None,
        detail_error: Exception | None = None,
    ) -> None:
        self.result = result or []
        self.error = error
        self.detail_error = detail_error
        self.closed = False
        self.detail_calls: list[JobPosting] = []

    def extract_jobs(self, url: str):
        if self.error:
            raise self.error
        return self.result

    def close(self) -> None:
        self.closed = True

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        self.detail_calls.append(job)
        if self.detail_error:
            raise self.detail_error
        return JobDetails(
            description_text="Build and operate reliable backend services.",
            location="Jakarta",
        )


class FakeSummarizer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[JobPosting, JobDetails]] = []
        self.closed = False

    def summarize(self, job: JobPosting, details: JobDetails) -> JobSummary:
        self.calls.append((job, details))
        if self.error:
            raise self.error
        return JobSummary(
            overview="Build reliable backend services.",
            responsibilities=["Design APIs"],
            requirements=["Backend engineering experience"],
            tech_stack=["Python"],
            location=details.location,
        )

    def close(self) -> None:
        self.closed = True


class FakeNotifier:
    def __init__(self, fail_errors: bool = False, fail_jobs: bool = False) -> None:
        self.fail_errors = fail_errors
        self.fail_jobs = fail_jobs
        self.error_alerts: list[tuple[str, str]] = []
        self.job_alerts: list[list[JobAlert]] = []

    def send_error_alert(self, company: str, details: str) -> None:
        self.error_alerts.append((company, details))
        if self.fail_errors:
            raise NotificationError("maintenance alert failed")

    def send_job_alerts(self, jobs: list[JobAlert]) -> None:
        self.job_alerts.append(jobs)
        if self.fail_jobs:
            raise NotificationError("job alert failed")


def target(name: str, scraper: FakeScraper) -> ScraperTarget:
    return ScraperTarget(name=name, url=f"https://example.test/{name}", factory=lambda: scraper)


def notified_postings(notifier: FakeNotifier) -> list[list[JobPosting]]:
    return [[alert.posting for alert in batch] for batch in notifier.job_alerts]


def test_registry_contains_all_requested_companies() -> None:
    assert [item.name for item in TARGETS] == [
        "Shopee",
        "Mekari",
        "Blibli",
        "StaffAny",
        "Amartha",
        "ByteDance",
        "Tiket",
        "Stockbit",
        "Traveloka",
        "DANA",
        "Telkom",
        "Grab",
        "GoTo",
        "Gojek",
    ]
    assert all(item.url.startswith("https://") for item in TARGETS)


def test_dom_failure_alerts_and_does_not_stop_later_companies(tmp_path: Path) -> None:
    failed = FakeScraper(error=DOMStructureChangedError("cards disappeared"))
    successful = FakeScraper(result=[posting()])
    notifier = FakeNotifier()
    state = StateManager(tmp_path / "seen.json")

    new_jobs = run(
        targets=[target("Broken Co", failed), target("Acme", successful)],
        state_manager=state,
        notifier=notifier,
        summarizer=None,
    )

    assert new_jobs == [posting()]
    assert notifier.error_alerts == [("Broken Co", "cards disappeared")]
    assert notified_postings(notifier) == [[posting()]]
    assert failed.closed and successful.closed
    assert state.get_new_jobs([posting()]) == []


def test_failed_maintenance_notification_still_allows_other_scrapers(tmp_path: Path) -> None:
    notifier = FakeNotifier(fail_errors=True)
    successful = FakeScraper(result=[posting()])

    new_jobs = run(
        targets=[
            target("Broken", FakeScraper(error=DOMStructureChangedError("bad DOM"))),
            target("Acme", successful),
        ],
        state_manager=StateManager(tmp_path / "seen.json"),
        notifier=notifier,
        summarizer=None,
    )

    assert new_jobs == [posting()]
    assert notified_postings(notifier) == [[posting()]]


def test_duplicate_results_are_alerted_once(tmp_path: Path) -> None:
    same_job = posting()
    notifier = FakeNotifier()

    new_jobs = run(
        targets=[
            target("First", FakeScraper(result=[same_job])),
            target("Second", FakeScraper(result=[same_job])),
        ],
        state_manager=StateManager(tmp_path / "seen.json"),
        notifier=notifier,
        summarizer=None,
    )

    assert new_jobs == [same_job]
    assert notified_postings(notifier) == [[same_job]]


def test_no_jobs_means_no_notification_or_state_write(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    notifier = FakeNotifier()

    assert run(
        targets=[target("Empty", FakeScraper())],
        state_manager=StateManager(path),
        notifier=notifier,
        summarizer=None,
    ) == []

    assert notifier.job_alerts == []
    assert not path.exists()


def test_job_notification_failure_leaves_state_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    state = StateManager(path)

    with pytest.raises(NotificationError, match="job alert failed"):
        run(
            targets=[target("Acme", FakeScraper(result=[posting()]))],
            state_manager=state,
            notifier=FakeNotifier(fail_jobs=True),
            summarizer=None,
        )

    assert not path.exists()
    assert state.get_new_jobs([posting()]) == [posting()]


def test_non_dom_scraper_failure_is_isolated(tmp_path: Path) -> None:
    notifier = FakeNotifier()

    new_jobs = run(
        targets=[
            target("Offline", FakeScraper(error=RuntimeError("temporary failure"))),
            target("Acme", FakeScraper(result=[posting()])),
        ],
        state_manager=StateManager(tmp_path / "seen.json"),
        notifier=notifier,
        summarizer=None,
    )

    assert new_jobs == [posting()]
    assert notifier.error_alerts == []


def test_scraper_factory_failure_is_isolated(tmp_path: Path) -> None:
    def broken_factory():
        raise RuntimeError("client construction failed")

    notifier = FakeNotifier()
    new_jobs = run(
        targets=[
            ScraperTarget(
                name="Broken",
                url="https://example.test/broken",
                factory=broken_factory,
            ),
            target("Acme", FakeScraper(result=[posting()])),
        ],
        state_manager=StateManager(tmp_path / "seen.json"),
        notifier=notifier,
        summarizer=None,
    )

    assert new_jobs == [posting()]
    assert notified_postings(notifier) == [[posting()]]


def test_seen_job_makes_no_detail_or_llm_calls(tmp_path: Path) -> None:
    job = posting()
    state = StateManager(tmp_path / "seen.json")
    state.mark_seen([job])
    scraper = FakeScraper(result=[job])
    summarizer = FakeSummarizer()

    result = run(
        targets=[target("Acme", scraper)],
        state_manager=state,
        notifier=FakeNotifier(),
        summarizer=summarizer,
    )

    assert result == []
    assert scraper.detail_calls == []
    assert summarizer.calls == []


def test_new_job_is_detailed_summarized_and_notified_once(tmp_path: Path) -> None:
    job = posting()
    scraper = FakeScraper(result=[job])
    summarizer = FakeSummarizer()
    notifier = FakeNotifier()
    state = StateManager(tmp_path / "seen.json")

    result = run(
        targets=[target("Acme", scraper)],
        state_manager=state,
        notifier=notifier,
        summarizer=summarizer,
    )

    assert result == [job]
    assert scraper.detail_calls == [job]
    assert [call[0] for call in summarizer.calls] == [job]
    alert = notifier.job_alerts[0][0]
    assert alert.summary is not None
    assert alert.summary.overview == "Build reliable backend services."
    assert state.get_new_jobs([job]) == []


def test_detail_structure_failure_sends_fallback_and_maintenance_alert(
    tmp_path: Path,
) -> None:
    job = posting()
    scraper = FakeScraper(
        result=[job],
        detail_error=JobDetailStructureChangedError("description selector missing"),
    )
    notifier = FakeNotifier()
    summarizer = FakeSummarizer()

    result = run(
        targets=[target("Acme", scraper)],
        state_manager=StateManager(tmp_path / "seen.json"),
        notifier=notifier,
        summarizer=summarizer,
    )

    assert result == [job]
    assert summarizer.calls == []
    assert notifier.error_alerts == [("Acme", "description selector missing")]
    assert notifier.job_alerts[0][0].summary is None
    assert notifier.job_alerts[0][0].summary_unavailable is True


def test_summarization_failure_does_not_hide_job_or_stop_later_jobs(
    tmp_path: Path,
) -> None:
    first, second = posting("1"), posting("2")
    notifier = FakeNotifier()
    failing = FakeSummarizer(error=SummarizationError("provider unavailable"))

    result = run(
        targets=[
            target("First", FakeScraper(result=[first])),
            target("Second", FakeScraper(result=[second])),
        ],
        state_manager=StateManager(tmp_path / "seen.json"),
        notifier=notifier,
        summarizer=failing,
    )

    assert result == [first, second]
    assert len(failing.calls) == 2
    assert all(alert.summary_unavailable for alert in notifier.job_alerts[0])
    assert notified_postings(notifier) == [[first, second]]


def test_summary_run_limit_falls_back_without_fetching_extra_details(
    tmp_path: Path,
) -> None:
    first, second = posting("1"), posting("2")
    scraper = FakeScraper(result=[first, second])
    notifier = FakeNotifier()
    summarizer = FakeSummarizer()

    run(
        targets=[target("Acme", scraper)],
        state_manager=StateManager(tmp_path / "seen.json"),
        notifier=notifier,
        summarizer=summarizer,
        max_summaries_per_run=1,
    )

    assert scraper.detail_calls == [first]
    assert len(summarizer.calls) == 1
    assert notifier.job_alerts[0][0].summary is not None
    assert notifier.job_alerts[0][1].summary_unavailable is True


def test_duplicate_new_job_is_enriched_only_once_across_targets(tmp_path: Path) -> None:
    same_job = posting()
    first_scraper = FakeScraper(result=[same_job])
    second_scraper = FakeScraper(result=[same_job])
    summarizer = FakeSummarizer()
    notifier = FakeNotifier()

    result = run(
        targets=[
            target("First", first_scraper),
            target("Second", second_scraper),
        ],
        state_manager=StateManager(tmp_path / "seen.json"),
        notifier=notifier,
        summarizer=summarizer,
    )

    assert result == [same_job]
    assert first_scraper.detail_calls == [same_job]
    assert second_scraper.detail_calls == []
    assert len(summarizer.calls) == 1
    assert notified_postings(notifier) == [[same_job]]


def test_repeated_detail_structure_failures_alert_company_only_once(
    tmp_path: Path,
) -> None:
    first, second = posting("1"), posting("2")
    scraper = FakeScraper(
        result=[first, second],
        detail_error=JobDetailStructureChangedError("description selector missing"),
    )
    notifier = FakeNotifier()

    result = run(
        targets=[target("Acme", scraper)],
        state_manager=StateManager(tmp_path / "seen.json"),
        notifier=notifier,
        summarizer=FakeSummarizer(),
    )

    assert result == [first, second]
    assert notifier.error_alerts == [
        ("Acme", "description selector missing")
    ]
    assert all(alert.summary_unavailable for alert in notifier.job_alerts[0])


def test_invalid_summary_limit_is_rejected_before_network_clients_are_built(
    monkeypatch,
) -> None:
    constructors: list[str] = []
    monkeypatch.setenv("OPENROUTER_MAX_SUMMARIES_PER_RUN", "not-an-integer")
    monkeypatch.setattr(
        main_module,
        "TelegramNotifier",
        lambda: constructors.append("telegram"),
    )
    monkeypatch.setattr(
        main_module,
        "OpenRouterSummarizer",
        lambda: constructors.append("openrouter"),
    )

    with pytest.raises(
        ValueError, match="OPENROUTER_MAX_SUMMARIES_PER_RUN must be an integer"
    ):
        run(targets=[])

    assert constructors == []


def test_missing_openrouter_key_keeps_basic_alerts_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    job = posting()
    notifier = FakeNotifier()

    def missing_key() -> None:
        raise ValueError("Missing required environment variable: OPENROUTER_API_KEY")

    monkeypatch.setattr(main_module, "OpenRouterSummarizer", missing_key)

    result = run(
        targets=[target("Acme", FakeScraper(result=[job]))],
        state_manager=StateManager(tmp_path / "seen.json"),
        notifier=notifier,
    )

    assert result == [job]
    assert notifier.job_alerts[0][0].posting == job
    assert notifier.job_alerts[0][0].summary_unavailable is True


def test_invalid_state_is_not_misreported_as_a_scraper_failure(tmp_path: Path) -> None:
    state_path = tmp_path / "seen.json"
    state_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid seen job state"):
        run(
            targets=[target("Acme", FakeScraper(result=[posting()]))],
            state_manager=StateManager(state_path),
            notifier=FakeNotifier(),
            summarizer=None,
        )
