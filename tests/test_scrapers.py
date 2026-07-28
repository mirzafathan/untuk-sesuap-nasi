from pathlib import Path

import httpx
import pytest

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobPosting
from scrapers.amartha import AmarthaScraper
from scrapers.base import BaseScraper, HTMLScraper
from scrapers.blibli import BlibliScraper
from scrapers.bytedance import ByteDanceScraper
from scrapers.dana import DanaScraper
from scrapers.gojek import GojekScraper
from scrapers.goto import GoToScraper
from scrapers.grab import GrabScraper
from scrapers.mekari import MekariScraper
from scrapers.shopee import ShopeeScraper
from scrapers.staffany import StaffanyScraper
from scrapers.stockbit import StockbitScraper
from scrapers.telkom import TelkomScraper
from scrapers.tiket import TiketScraper
from scrapers.traveloka import TravelokaScraper


FIXTURES = Path(__file__).parents[1] / "inspect-elements-outerhtml"


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def test_base_scraper_is_abstract() -> None:
    with pytest.raises(TypeError):
        BaseScraper()  # type: ignore[abstract]


def test_html_scraper_fetches_and_parses_with_injected_client() -> None:
    class ExampleScraper(HTMLScraper):
        company = "Example"

        def parse_html(self, html: str, source_url: str):
            assert html == "<div>ok</div>"
            assert source_url == "https://example.test/jobs"
            return []

    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<div>ok</div>"))
    scraper = ExampleScraper(client=httpx.Client(transport=transport))

    assert scraper.extract_jobs("https://example.test/jobs") == []


def test_scraper_without_detail_contract_fails_explicitly() -> None:
    class ListingOnlyScraper(HTMLScraper):
        company = "Example"

        def parse_html(self, html: str, source_url: str):
            return []

    job = JobPosting(
        id="1",
        title="Backend Engineer",
        company="Example",
        url="https://example.test/jobs/1",
    )

    with pytest.raises(JobDetailStructureChangedError, match="not implemented"):
        ListingOnlyScraper().extract_job_details(job)


@pytest.mark.parametrize(
    ("scraper", "name", "expected_count"),
    [
        (MekariScraper(), "mekari", 0),
        (StaffanyScraper(), "staffany", 1),
        (AmarthaScraper(), "amartha", 2),
        (ByteDanceScraper(), "bytedance", 1),
        (TiketScraper(), "tiket", 2),
        (StockbitScraper(), "stockbit", 8),
        (TravelokaScraper(), "traveloka", 4),
        (TelkomScraper(), "telkom", 0),
        (GrabScraper(), "grab", 9),
        (GoToScraper(), "goto", 0),
        (GojekScraper(), "gojek", 0),
    ],
)
def test_saved_outer_html_contracts(scraper, name: str, expected_count: int) -> None:
    jobs = scraper.parse_html(fixture(name), f"https://example.test/{name}")

    assert len(jobs) == expected_count
    assert all(job.company.casefold() == scraper.company.casefold() for job in jobs)
    assert all(job.url.startswith("http") for job in jobs)


@pytest.mark.parametrize(
    ("scraper", "name"),
    [
        (ShopeeScraper(), "shopee"),
        (BlibliScraper(), "blibli"),
        (DanaScraper(), "dana"),
    ],
)
def test_incomplete_saved_dom_raises_maintenance_error(scraper, name: str) -> None:
    with pytest.raises(DOMStructureChangedError):
        scraper.parse_html(fixture(name), f"https://example.test/{name}")


@pytest.mark.parametrize(
    "scraper",
    [
        MekariScraper(),
        StaffanyScraper(),
        AmarthaScraper(),
        ByteDanceScraper(),
        TiketScraper(),
        StockbitScraper(),
        TravelokaScraper(),
        GrabScraper(),
        GoToScraper(),
        GojekScraper(),
    ],
)
def test_missing_expected_dom_raises(scraper) -> None:
    with pytest.raises(DOMStructureChangedError):
        scraper.parse_html("<html><body>unexpected</body></html>", "https://example.test/jobs")


def test_shopee_api_payload_is_filtered_and_mapped() -> None:
    payload = {
        "job_list": [
            {"id": 42, "job_name": "Senior Backend Engineer"},
            {"id": 43, "job_name": "Product Manager"},
        ],
        "total_count": 2,
    }

    jobs = ShopeeScraper().parse_api_payload(payload)

    assert [(job.id, job.title, job.url) for job in jobs] == [
        ("42", "Senior Backend Engineer", "https://careers.shopee.co.id/job-detail/42")
    ]


def test_blibli_api_payload_uses_only_active_open_postings() -> None:
    payload = {
        "responseObject": [
            {
                "jobCode": "GDN-123",
                "jobName": "Software Engineer II",
                "companyName": "PT Global Digital Niaga",
                "active": True,
                "postingStatus": True,
                "recruitmentStatus": "Open",
            },
            {
                "jobCode": "GDN-OLD",
                "jobName": "Backend Engineer",
                "active": False,
                "postingStatus": False,
                "recruitmentStatus": "Closed",
            },
        ]
    }

    jobs = BlibliScraper().parse_api_payload(payload)

    assert len(jobs) == 1
    assert jobs[0].id == "GDN-123"
    assert jobs[0].company == "Blibli"
    assert jobs[0].url == "https://careers.blibli.com/job-detail/software-engineer-ii?job=GDN-123"


def test_blibli_requests_json_instead_of_xml_content_negotiation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/json"
        return httpx.Response(
            200, json={"responseObject": [], "paginationToken": None}
        )

    scraper = BlibliScraper(client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert scraper.extract_jobs("https://careers.blibli.com/jobs") == []


def test_amartha_api_payload_maps_date_and_url() -> None:
    payload = {
        "jobs": [
            {
                "shortcode": "ABC123",
                "title": "Principal Backend Engineer",
                "url": "https://apply.workable.com/j/ABC123",
                "published_on": "2026-07-20",
            }
        ]
    }

    jobs = AmarthaScraper().parse_api_payload(payload)

    assert jobs[0].posted_date == "2026-07-20"
    assert jobs[0].id == "ABC123"


def test_bytedance_api_payload_maps_detail_url() -> None:
    payload = {
        "code": 0,
        "data": {
            "count": 1,
            "job_post_list": [{"id": "7001", "title": "AI Engineer"}],
        },
    }

    jobs = ByteDanceScraper().parse_api_payload(payload)

    assert jobs[0].url == "https://joinbytedance.com/search/7001"


def test_tiket_api_payload_maps_workday_fields() -> None:
    payload = {
        "total": 1,
        "jobPostings": [
            {
                "title": "Software Engineer I",
                "externalPath": "/job/Jakarta/Software-Engineer-I_R-1",
                "postedOn": "Posted Yesterday",
                "bulletFields": ["R-1"],
            }
        ],
    }

    jobs = TiketScraper().parse_api_payload(payload)

    assert jobs[0].id == "R-1"
    assert jobs[0].posted_date == "Posted Yesterday"
    assert jobs[0].url.endswith("/Tiket_Careers/job/Jakarta/Software-Engineer-I_R-1")


def test_traveloka_api_payload_converts_timestamp_to_date() -> None:
    payload = {
        "data": {
            "page": 1,
            "lastPage": 1,
            "jobs": [
                {
                    "requisitionId": "mj001",
                    "title": "LLM Engineer",
                    "link": "/jobs/mj001-llm-engineer",
                    "createdAt": 1_720_742_400_000,
                }
            ],
        }
    }

    jobs = TravelokaScraper().parse_api_payload(payload)

    assert jobs[0].posted_date == "2024-07-12"
    assert jobs[0].url == "https://careers.traveloka.com/jobs/mj001-llm-engineer"


def test_dana_api_payload_flattens_departments() -> None:
    payload = {
        "status": 200,
        "data": [
            {
                "name": "Technology",
                "jobs": [
                    {"jobId": "job-1", "title": "ML Engineer"},
                    {"jobId": "job-2", "title": "Designer"},
                ],
            }
        ],
    }

    jobs = DanaScraper().parse_api_payload(payload)

    assert [(job.id, job.url) for job in jobs] == [
        ("job-1", "https://www.career.dana.id/jobs/job-1")
    ]


def goto_api_payload(company_code: str = "HoldCo") -> dict:
    return {
        "code": "200000",
        "data": {
            "page": 1,
            "total": 2,
            "items": [
                {
                    "parent_department": f"{company_code} - Engineering",
                    "job_list": [
                        {
                            "id": "job-1",
                            "text": "Senior Backend Engineer",
                            "state": "published",
                            "distributionChannels": ["public"],
                        },
                        {
                            "id": "job-2",
                            "text": "Product Manager",
                            "state": "published",
                            "distributionChannels": ["public"],
                        },
                    ],
                }
            ],
        },
    }


def test_goto_api_payload_maps_and_filters_complete_grouped_results() -> None:
    jobs = GoToScraper().parse_api_payload(goto_api_payload())

    assert [(job.id, job.title, job.url) for job in jobs] == [
        (
            "job-1",
            "Senior Backend Engineer",
            "https://www.gotocompany.com/en/careers/job-1",
        )
    ]


def test_gojek_api_payload_maps_and_filters_complete_grouped_results() -> None:
    jobs = GojekScraper().parse_api_payload(goto_api_payload("ODS"))

    assert [(job.id, job.title, job.url) for job in jobs] == [
        (
            "job-1",
            "Senior Backend Engineer",
            "https://www.gojek.io/careers/view/senior-backend-engineer/job-1",
        )
    ]


def test_telkom_api_payload_supports_valid_empty_and_jobs() -> None:
    scraper = TelkomScraper()
    assert scraper.parse_api_payload({"data": {"total": 0, "data": []}}) == []

    jobs = scraper.parse_api_payload(
        {
            "data": {
                "total": 1,
                "data": [
                    {
                        "name": "Backend Engineer",
                        "short_desc_url": "backend-engineer-987",
                    }
                ],
            }
        }
    )

    assert jobs[0].id == "987"
    assert jobs[0].url == "https://careers.telkom.co.id/detail-job/backend-engineer-987"


def test_telkom_discovers_the_public_browser_api_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/search-jobs":
            return httpx.Response(
                200,
                text='<script src="/main~app.js?v=1_03"></script>',
            )
        if path == "/main~app.js":
            return httpx.Response(200, text='8:"0123456789abcdefabcd"')
        if path == "/8.0123456789abcdefabcd.js":
            return httpx.Response(
                200,
                text='username:"public-user",password:"public-password"',
            )
        if path.endswith("/job/search"):
            assert request.headers["authorization"] == (
                "Basic cHVibGljLXVzZXI6cHVibGljLXBhc3N3b3Jk"
            )
            return httpx.Response(
                200,
                json={"data": {"total": 0, "data": [], "last_page": 1}},
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    scraper = TelkomScraper(client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert scraper.extract_jobs("https://careers.telkom.co.id/search-jobs") == []


@pytest.mark.parametrize(
    ("scraper", "payload"),
    [
        (ShopeeScraper(), {}),
        (BlibliScraper(), {}),
        (AmarthaScraper(), {}),
        (ByteDanceScraper(), {"code": 0, "data": {}}),
        (TiketScraper(), {}),
        (TravelokaScraper(), {"data": {}}),
        (DanaScraper(), {"status": 200}),
        (TelkomScraper(), {}),
        (GoToScraper(), {}),
        (GojekScraper(), {}),
    ],
)
def test_api_schema_changes_raise(scraper, payload) -> None:
    with pytest.raises(DOMStructureChangedError):
        scraper.parse_api_payload(payload)
