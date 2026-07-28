import base64
import json

import httpx
import pytest

from exceptions import JobDetailStructureChangedError
from models import JobPosting
from scrapers.gojek import GojekScraper
from scrapers.goto import GoToScraper
from scrapers.grab import GrabScraper
from scrapers.telkom import TelkomScraper


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def posting(company: str, job_id: str, url: str) -> JobPosting:
    return JobPosting(
        id=job_id,
        title="Senior Software Engineer",
        company=company,
        url=url,
    )


def test_grab_extracts_structured_detail_and_metadata() -> None:
    job = posting(
        "Grab",
        "744000140061339",
        "https://www.grab.careers/en/jobs/744000140061339/senior-software-engineer/",
    )
    schema = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "description": (
            "<p>Build reliable payment services.</p>"
            "<h2>Qualifications</h2><ul><li>Five years of Python experience</li></ul>"
        ),
        "employmentType": "FULL_TIME",
        "jobLocation": {
            "@type": "Place",
            "name": "Singapore",
            "address": {"addressLocality": "Singapore", "addressCountry": "SG"},
        },
        "baseSalary": {
            "currency": "SGD",
            "value": {"minValue": 8000, "maxValue": 10000, "unitText": "MONTH"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == job.url
        assert "Mozilla/5.0" in request.headers["User-Agent"]
        html = (
            '<html><script id="js-job-posting" type="application/ld+json">'
            f"{json.dumps(schema)}</script></html>"
        )
        return httpx.Response(200, text=html)

    details = GrabScraper(client=mock_client(handler)).extract_job_details(job)

    assert details.description_text == (
        "Build reliable payment services.\nQualifications\nFive years of Python experience"
    )
    assert details.location == "Singapore"
    assert details.employment_type == "Full-time"
    assert details.work_arrangement is None
    assert details.salary == "SGD 8,000–10,000 per month"


@pytest.mark.parametrize(
    "html",
    [
        "<html><main>Job content moved</main></html>",
        '<script id="js-job-posting" type="application/ld+json">not-json</script>',
        (
            '<script id="js-job-posting" type="application/ld+json">'
            '{"@type":"JobPosting","description":"   "}</script>'
        ),
        (
            '<script id="js-job-posting" type="application/ld+json">'
            '{"@type":"BreadcrumbList","description":"Build APIs"}</script>'
        ),
    ],
)
def test_grab_rejects_missing_or_malformed_structured_detail(html: str) -> None:
    job = posting("Grab", "1", "https://www.grab.careers/en/jobs/1/backend/")
    scraper = GrabScraper(
        client=mock_client(lambda request: httpx.Response(200, text=html))
    )

    with pytest.raises(JobDetailStructureChangedError, match="Grab"):
        scraper.extract_job_details(job)


class DetailTestTelkomScraper(TelkomScraper):
    def _discover_public_api_auth(self, careers_url: str) -> tuple[str, str]:
        assert careers_url == "https://careers.telkom.co.id/search-jobs"
        return "public-user", "public-password"


def test_telkom_fetches_detail_api_and_combines_all_job_content() -> None:
    job = posting(
        "Telkom",
        "42",
        "https://careers.telkom.co.id/detail-job/senior-software-engineer-42",
    )
    payload = {
        "message": "success",
        "data": {
            "name": "Senior Software Engineer",
            "long_desc": "Build national-scale platforms.",
            "job_desc": [
                {"id": 2, "desc_sequence": 2, "desc": "Review production changes"},
                {"id": 1, "desc_sequence": 1, "desc": "Design backend services"},
            ],
            "job_requirement": [
                {
                    "id": 1,
                    "requirement_sequence": 1,
                    "requirement": "Strong Python skills",
                }
            ],
            "location": {"name": "Jakarta"},
            "job_type": {"name": "Full Time"},
        },
        "code": 200,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == httpx.URL(
            "https://apicareers.telkom.co.id/v1/frontend/en/job/"
            "detail-slug/senior-software-engineer-42"
        )
        expected = base64.b64encode(b"public-user:public-password").decode()
        assert request.headers["Authorization"] == f"Basic {expected}"
        assert request.headers["Origin"] == "https://careers.telkom.co.id"
        return httpx.Response(200, json=payload)

    details = DetailTestTelkomScraper(
        client=mock_client(handler)
    ).extract_job_details(job)

    assert details.description_text == (
        "Build national-scale platforms.\n"
        "Responsibilities\n"
        "- Design backend services\n"
        "- Review production changes\n"
        "Requirements\n"
        "- Strong Python skills"
    )
    assert details.location == "Jakarta"
    assert details.employment_type == "Full Time"
    assert details.work_arrangement is None
    assert details.salary is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": None},
        {"data": {"long_desc": "Build APIs", "job_desc": {}, "job_requirement": []}},
        {
            "data": {
                "long_desc": "Build APIs",
                "job_desc": [{"desc_sequence": 1}],
                "job_requirement": [],
            }
        },
        {"data": {"long_desc": " ", "job_desc": [], "job_requirement": []}},
    ],
)
def test_telkom_rejects_changed_detail_schema(payload: dict) -> None:
    job = posting(
        "Telkom",
        "42",
        "https://careers.telkom.co.id/detail-job/backend-engineer-42",
    )
    scraper = DetailTestTelkomScraper(
        client=mock_client(lambda request: httpx.Response(200, json=payload))
    )

    with pytest.raises(JobDetailStructureChangedError, match="Telkom"):
        scraper.extract_job_details(job)


def test_telkom_rejects_a_job_url_outside_its_detail_route() -> None:
    job = posting("Telkom", "42", "https://careers.telkom.co.id/search-jobs")
    scraper = DetailTestTelkomScraper(
        client=mock_client(lambda request: pytest.fail("must not make a request"))
    )

    with pytest.raises(JobDetailStructureChangedError, match="detail URL"):
        scraper.extract_job_details(job)


def goinfra_payload(job_id: str) -> dict:
    return {
        "code": "200000",
        "data": {
            "page": 1,
            "total": 1,
            "items": [
                {
                    "parent_department": "Engineering",
                    "job_list": [
                        {
                            "id": job_id,
                            "text": "Senior Software Engineer",
                            "state": "published",
                            "distributionChannels": ["internal", "public"],
                            "categories": {
                                "commitment": "Permanent",
                                "location": "Jakarta",
                            },
                            "content": {
                                "descriptionHtml": "<p>Build reliable APIs.</p>",
                                "lists": [
                                    {
                                        "text": "What You Will Do",
                                        "content": (
                                            "<ul><li>Own backend services</li>"
                                            "<li>Improve observability</li></ul>"
                                        ),
                                    },
                                    {
                                        "text": "What You Will Need",
                                        "content": "<ul><li>Strong Go skills</li></ul>",
                                    },
                                ],
                                "closingHtml": "<p>Generic company marketing text.</p>",
                            },
                            "full_location": "Jakarta (ID)",
                            "job_detail": {
                                "employee_type": "Permanent",
                                "full_location": "Jakarta (ID)",
                            },
                        }
                    ],
                }
            ],
        },
    }


@pytest.mark.parametrize(
    ("scraper_type", "company", "company_code", "origin", "url"),
    [
        (
            GoToScraper,
            "GoTo",
            "HoldCo",
            "https://www.gotocompany.com",
            "https://www.gotocompany.com/en/careers/job-123",
        ),
        (
            GojekScraper,
            "Gojek",
            "ODS",
            "https://www.gojek.io",
            "https://www.gojek.io/careers/view/senior-software-engineer/job-123",
        ),
    ],
)
def test_goinfra_scrapers_find_job_and_extract_embedded_detail(
    scraper_type,
    company: str,
    company_code: str,
    origin: str,
    url: str,
) -> None:
    job = posting(company, "job-123", url)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.copy_remove_param("company").copy_remove_param(
            "search"
        ).copy_remove_param("location").copy_remove_param("department") == httpx.URL(
            "https://content.goinfra.co.id/ent-hris/career/job"
        )
        assert dict(request.url.params) == {
            "company": company_code,
            "search": "",
            "location": "",
            "department": "",
        }
        assert request.headers["Origin"] == origin
        return httpx.Response(200, json=goinfra_payload(job.id))

    details = scraper_type(client=mock_client(handler)).extract_job_details(job)

    assert details.description_text == (
        "Build reliable APIs.\n"
        "What You Will Do\n"
        "Own backend services\n"
        "Improve observability\n"
        "What You Will Need\n"
        "Strong Go skills"
    )
    assert "Generic company marketing text" not in details.description_text
    assert details.location == "Jakarta (ID)"
    assert details.employment_type == "Permanent"
    assert details.work_arrangement is None
    assert details.salary is None


@pytest.mark.parametrize("scraper_type", [GoToScraper, GojekScraper])
def test_goinfra_detail_fails_when_requested_job_is_absent(scraper_type) -> None:
    payload = goinfra_payload("another-job")
    scraper = scraper_type(
        client=mock_client(lambda request: httpx.Response(200, json=payload))
    )
    job = posting(scraper.company, "missing-job", scraper.job_url("missing-job", job_title()))

    with pytest.raises(JobDetailStructureChangedError, match="no longer present"):
        scraper.extract_job_details(job)


def job_title() -> str:
    return "Senior Software Engineer"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_content",
        "bad_lists",
        "bad_list_item",
        "empty_content",
        "duplicate_job",
    ],
)
def test_goinfra_detail_rejects_malformed_or_ambiguous_content(mutation: str) -> None:
    payload = goinfra_payload("job-123")
    records = payload["data"]["items"][0]["job_list"]
    record = records[0]
    if mutation == "missing_content":
        record.pop("content")
    elif mutation == "bad_lists":
        record["content"]["lists"] = {}
    elif mutation == "bad_list_item":
        record["content"]["lists"] = [{"text": "Requirements"}]
    elif mutation == "empty_content":
        record["content"] = {"descriptionHtml": " ", "lists": []}
    elif mutation == "duplicate_job":
        records.append(record.copy())

    scraper = GoToScraper(
        client=mock_client(lambda request: httpx.Response(200, json=payload))
    )
    job = posting(
        "GoTo", "job-123", "https://www.gotocompany.com/en/careers/job-123"
    )

    with pytest.raises(JobDetailStructureChangedError, match="GoTo"):
        scraper.extract_job_details(job)
