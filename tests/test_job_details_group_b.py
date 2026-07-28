import httpx
import pytest

from exceptions import JobDetailStructureChangedError
from models import JobPosting
from scrapers.bytedance import ByteDanceScraper
from scrapers.dana import DanaScraper
from scrapers.stockbit import StockbitScraper
from scrapers.tiket import TiketScraper
from scrapers.traveloka import TravelokaScraper


def job(company: str, job_id: str, url: str) -> JobPosting:
    return JobPosting(
        id=job_id,
        title="Senior Backend Engineer",
        company=company,
        url=url,
    )


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_bytedance_detail_page_maps_sections_and_metadata() -> None:
    posting = job(
        "ByteDance",
        "7001",
        "https://joinbytedance.com/search/7001",
    )
    html = """
    <main>
      <div><p>Location:</p><p>Singapore</p></div>
      <div><p>Employment Type:</p><p>Regular</p></div>
      <section>
        <p class="bd-title">Responsibilities</p>
        <p>Build reliable APIs.\n- Operate distributed services.</p>
      </section>
      <section>
        <p class="bd-title">Qualifications</p>
        <p>Five years of experience.\n- Strong Python skills.</p>
      </section>
      <section>
        <p class="bd-title">Job Information</p>
        <div>The base salary range for this position is $120,000 - $180,000 annually.</div>
      </section>
    </main>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == posting.url
        return httpx.Response(200, text=html)

    details = ByteDanceScraper(client=client(handler)).extract_job_details(posting)

    assert details.description_text == (
        "Responsibilities\nBuild reliable APIs.\n- Operate distributed services.\n\n"
        "Qualifications\nFive years of experience.\n- Strong Python skills."
    )
    assert details.location == "Singapore"
    assert details.employment_type == "Regular"
    assert details.salary == "$120,000 - $180,000 annually"


def test_bytedance_detail_page_requires_both_job_sections() -> None:
    posting = job("ByteDance", "7001", "https://joinbytedance.com/search/7001")
    html = '<p class="bd-title">Responsibilities</p><p>Build APIs.</p>'
    scraper = ByteDanceScraper(
        client=client(lambda request: httpx.Response(200, text=html))
    )

    with pytest.raises(JobDetailStructureChangedError, match="Qualifications"):
        scraper.extract_job_details(posting)


def test_tiket_uses_workday_detail_endpoint_and_cleans_description_html() -> None:
    posting = job(
        "Tiket",
        "R-3221",
        "https://tiketdotcom.wd3.myworkdayjobs.com/Tiket_Careers/job/"
        "Jakarta-Indonesia/Software-Engineer-I_R-3221",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == (
            "https://tiketdotcom.wd3.myworkdayjobs.com/wday/cxs/tiketdotcom/"
            "Tiket_Careers/job/Jakarta-Indonesia/Software-Engineer-I_R-3221"
        )
        return httpx.Response(
            200,
            json={
                "jobPostingInfo": {
                    "jobReqId": "R-3221",
                    "jobDescription": (
                        "<p>Build travel services.</p><p><b>Requirements</b></p>"
                        "<ul><li>Strong Java skills</li><li>Know SQL</li></ul>"
                    ),
                    "location": "Jakarta, Indonesia",
                    "timeType": "Full time",
                }
            },
        )

    details = TiketScraper(client=client(handler)).extract_job_details(posting)

    assert details.description_text == (
        "Build travel services.\nRequirements\n- Strong Java skills\n- Know SQL"
    )
    assert details.location == "Jakarta, Indonesia"
    assert details.employment_type == "Full time"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"jobPostingInfo": {"jobReqId": "R-1", "jobDescription": ""}},
        {
            "jobPostingInfo": {
                "jobReqId": "WRONG",
                "jobDescription": "<p>Build APIs.</p>",
            }
        },
    ],
)
def test_tiket_rejects_malformed_or_wrong_detail_payload(payload: dict) -> None:
    posting = job(
        "Tiket",
        "R-1",
        "https://tiketdotcom.wd3.myworkdayjobs.com/Tiket_Careers/job/ID/Role_R-1",
    )
    scraper = TiketScraper(
        client=client(lambda request: httpx.Response(200, json=payload))
    )

    with pytest.raises(JobDetailStructureChangedError):
        scraper.extract_job_details(posting)


def test_stockbit_uses_detail_api_and_maps_exact_metadata() -> None:
    posting = job(
        "Stockbit",
        "7309589",
        "https://careers.stockbit.com/jobs/7309589",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://careers.stockbit.com/api/v2/jobs/7309589"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "id": "7309589",
                    "description": (
                        "<p>Build production vision systems.</p>"
                        "<p><strong>Qualifications</strong></p>"
                        "<ul><li>Python</li><li>PyTorch</li></ul>"
                    ),
                    "city": "Jakarta",
                    "region": "Indonesia",
                    "employment-type": "Full-time",
                    "salary": "IDR 30m - 40m",
                },
            },
        )

    details = StockbitScraper(client=client(handler)).extract_job_details(posting)

    assert details.description_text == (
        "Build production vision systems.\nQualifications\n- Python\n- PyTorch"
    )
    assert details.location == "Jakarta, Indonesia"
    assert details.employment_type == "Full-time"
    assert details.salary == "IDR 30m - 40m"


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False, "data": {}},
        {"success": True, "data": {"id": "1"}},
        {"success": True, "data": {"id": "wrong", "description": "Build APIs"}},
    ],
)
def test_stockbit_rejects_changed_detail_schema(payload: dict) -> None:
    posting = job("Stockbit", "1", "https://careers.stockbit.com/jobs/1")
    scraper = StockbitScraper(
        client=client(lambda request: httpx.Response(200, json=payload))
    )

    with pytest.raises(JobDetailStructureChangedError):
        scraper.extract_job_details(posting)


def test_traveloka_fetches_and_parses_server_rendered_detail_page() -> None:
    posting = job(
        "Traveloka",
        "mj000233-backend-engineer",
        "https://careers.traveloka.com/jobs/mj000233-backend-engineer",
    )
    html = """
    <main>
      <div><span>Job Type</span><span>Regular</span></div>
      <div><span>Location</span><div><span>BSD City, Indonesia</span></div></div>
      <section>
        <h1>Job Description</h1>
        <span>
          <p>Build high-performance travel systems.</p>
          <p><strong>Responsibilities</strong></p>
          <ul><li>Design backend services</li></ul>
          <p><strong>Requirements</strong></p>
          <ul><li>Five years of experience</li></ul>
        </span>
      </section>
    </main>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == posting.url
        return httpx.Response(200, text=html)

    details = TravelokaScraper(client=client(handler)).extract_job_details(posting)

    assert details.description_text == (
        "Build high-performance travel systems.\nResponsibilities\n"
        "- Design backend services\nRequirements\n- Five years of experience"
    )
    assert details.location == "BSD City, Indonesia"
    assert details.employment_type == "Regular"


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>Unexpected page</body></html>",
        "<h1>Job Description</h1><span></span>",
    ],
)
def test_traveloka_rejects_missing_or_empty_detail_container(html: str) -> None:
    posting = job("Traveloka", "mj1", "https://careers.traveloka.com/jobs/mj1")
    scraper = TravelokaScraper(
        client=client(lambda request: httpx.Response(200, text=html))
    )

    with pytest.raises(JobDetailStructureChangedError):
        scraper.extract_job_details(posting)


def test_dana_uses_detail_api_and_combines_structured_job_sections() -> None:
    posting = job(
        "DANA",
        "dana-1",
        "https://www.career.dana.id/jobs/dana-1",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://api.career.dana.id/api/career/job/dana-1"
        return httpx.Response(
            200,
            json={
                "status": 200,
                "data": {
                    "job": {
                        "jobId": "dana-1",
                        "description": "Build reliable payment services.",
                        "responsibilities": ["Design APIs", "Operate services"],
                        "requirements": ["Strong Go skills", "Know PostgreSQL"],
                        "location": "Jakarta",
                        "employmentType": "FULL_TIME",
                    }
                },
            },
        )

    details = DanaScraper(client=client(handler)).extract_job_details(posting)

    assert details.description_text == (
        "Build reliable payment services.\n\nResponsibilities\n- Design APIs\n"
        "- Operate services\n\nRequirements\n- Strong Go skills\n- Know PostgreSQL"
    )
    assert details.location == "Jakarta"
    assert details.employment_type == "Full-time"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": 500, "data": {}},
        {"status": 200, "data": {"job": {"jobId": "dana-1"}}},
        {
            "status": 200,
            "data": {
                "job": {
                    "jobId": "wrong",
                    "description": "Build APIs",
                    "responsibilities": [],
                    "requirements": [],
                }
            },
        },
        {
            "status": 200,
            "data": {
                "job": {
                    "jobId": "dana-1",
                    "description": "Build APIs",
                    "responsibilities": "not-a-list",
                    "requirements": [],
                }
            },
        },
    ],
)
def test_dana_rejects_changed_detail_schema(payload: dict) -> None:
    posting = job("DANA", "dana-1", "https://www.career.dana.id/jobs/dana-1")
    scraper = DanaScraper(
        client=client(lambda request: httpx.Response(200, json=payload))
    )

    with pytest.raises(JobDetailStructureChangedError):
        scraper.extract_job_details(posting)


@pytest.mark.parametrize(
    ("scraper_type", "posting"),
    [
        (
            TiketScraper,
            job(
                "Tiket",
                "R-1",
                "https://tiketdotcom.wd3.myworkdayjobs.com/"
                "Tiket_Careers/job/ID/Role_R-1",
            ),
        ),
        (
            StockbitScraper,
            job("Stockbit", "1", "https://careers.stockbit.com/jobs/1"),
        ),
        (
            DanaScraper,
            job("DANA", "1", "https://www.career.dana.id/jobs/1"),
        ),
    ],
)
def test_json_detail_endpoints_reject_non_json_responses(
    scraper_type, posting: JobPosting
) -> None:
    scraper = scraper_type(
        client=client(
            lambda request: httpx.Response(
                200,
                text="<html>temporary upstream page</html>",
            )
        )
    )

    with pytest.raises(JobDetailStructureChangedError, match="not JSON"):
        scraper.extract_job_details(posting)
