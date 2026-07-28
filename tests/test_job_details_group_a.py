import html
import json

import httpx
import pytest

from exceptions import JobDetailStructureChangedError
from models import JobPosting
from scrapers.amartha import AmarthaScraper
from scrapers.blibli import BlibliScraper
from scrapers.mekari import MekariScraper
from scrapers.shopee import ShopeeScraper
from scrapers.staffany import StaffanyScraper


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def posting(company: str, *, job_id: str = "job-123", url: str | None = None) -> JobPosting:
    return JobPosting(
        id=job_id,
        title="Senior Backend Engineer",
        company=company,
        url=url or f"https://example.test/jobs/{job_id}",
    )


def test_shopee_fetches_and_cleans_first_party_detail_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/positions/detail/"
        assert request.url.params.get("id") == "128434"
        return httpx.Response(
            200,
            json={
                "id": 128434,
                "position_name": "Senior Backend Engineer",
                "position_presentation": [
                    {
                        "lang_code": "en",
                        "sub_team_description": "<p>Build logistics systems.</p>",
                        "job_description": "<ul><li>Design APIs</li><li>Own services</li></ul>",
                        "job_requirement": "<ul><li>Strong Go skills</li></ul>",
                    }
                ],
            },
        )

    details = ShopeeScraper(client=client(handler)).extract_job_details(
        posting("Shopee", job_id="128434")
    )

    assert "Build logistics systems." in details.description_text
    assert "Responsibilities\nDesign APIs\nOwn services" in details.description_text
    assert "Requirements\nStrong Go skills" in details.description_text


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 128434, "position_presentation": []},
        {
            "id": 128434,
            "position_presentation": [
                {"lang_code": "en", "job_description": "", "job_requirement": ""}
            ],
        },
    ],
)
def test_shopee_rejects_missing_or_empty_detail_content(payload: dict) -> None:
    scraper = ShopeeScraper(
        client=client(lambda request: httpx.Response(200, json=payload))
    )

    with pytest.raises(JobDetailStructureChangedError, match="Shopee"):
        scraper.extract_job_details(posting("Shopee", job_id="128434"))


def test_mekari_extracts_json_ld_description_and_exact_metadata() -> None:
    description = html.escape(
        "<p>Build reliable payroll services.</p>"
        "<h3>Requirements</h3><ul><li>Three years of Python</li></ul>"
    )
    payload = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Senior Backend Engineer",
        "description": description,
        "employmentType": "FULL_TIME",
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Jakarta",
                "addressRegion": "Jakarta",
                "addressCountry": "Indonesia",
            },
        },
    }
    body = f"""
        <html><head><script type="application/ld+json">{json.dumps(payload)}</script></head>
        <body><header><p class="opening-info">
          <span class="meta-job-location-city">Jakarta</span>
          <span>| Technology</span><span>| Full-time</span><span>| Partially remote</span>
        </p></header></body></html>
    """
    job = posting(
        "Mekari",
        job_id="fk123",
        url="https://mekari.hire.trakstar.com/jobs/fk123/",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == job.url
        return httpx.Response(200, text=body)

    details = MekariScraper(client=client(handler)).extract_job_details(job)

    assert details.description_text == (
        "Build reliable payroll services.\nRequirements\nThree years of Python"
    )
    assert details.location == "Jakarta, Indonesia"
    assert details.work_arrangement == "Partially remote"
    assert details.employment_type == "FULL_TIME"


@pytest.mark.parametrize(
    "body",
    [
        "<html><body>No structured job</body></html>",
        '<script type="application/ld+json">{"@type":"JobPosting","description":""}</script>',
        '<script type="application/ld+json">not-json</script>',
    ],
)
def test_mekari_rejects_missing_or_malformed_job_detail_json_ld(body: str) -> None:
    scraper = MekariScraper(
        client=client(lambda request: httpx.Response(200, text=body))
    )

    with pytest.raises(JobDetailStructureChangedError, match="Mekari"):
        scraper.extract_job_details(posting("Mekari"))


def test_blibli_fetches_per_job_api_and_cleans_summary_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ext/api/job/GDN-123"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(
            200,
            json={
                "responseObject": {
                    "jobCode": "GDN-123",
                    "jobName": "Senior Backend Engineer",
                    "location": "Jakarta",
                    "employmentType": "Permanent",
                    "jobSummary": (
                        "<h2>About the role</h2><p>Build checkout services.</p>"
                        "<h3>Qualifications</h3><ul><li>Strong Java skills</li></ul>"
                    ),
                },
                "status": {"code": 1000, "desc": "SUCCESS"},
            },
        )

    details = BlibliScraper(client=client(handler)).extract_job_details(
        posting("Blibli", job_id="GDN-123")
    )

    assert details.description_text == (
        "About the role\nBuild checkout services.\nQualifications\nStrong Java skills"
    )
    assert details.location == "Jakarta"
    assert details.employment_type == "Permanent"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": {"code": 1000}, "responseObject": None},
        {
            "status": {"code": 1000},
            "responseObject": {"jobCode": "GDN-123", "jobSummary": "<p> </p>"},
        },
        {"status": {"code": 5000}, "responseObject": {}},
    ],
)
def test_blibli_rejects_missing_or_malformed_job_detail_payload(payload: dict) -> None:
    scraper = BlibliScraper(
        client=client(lambda request: httpx.Response(200, json=payload))
    )

    with pytest.raises(JobDetailStructureChangedError, match="Blibli"):
        scraper.extract_job_details(posting("Blibli", job_id="GDN-123"))


def test_staffany_extracts_role_sections_and_compensation() -> None:
    body = """
      <main>
        <section class="elementor-top-section">
          <h2 class="elementor-heading-title">Senior Backend Engineer</h2>
        </section>
        <section class="elementor-top-section">
          <h2 class="elementor-heading-title">About the role</h2>
          <p>This is a remote position on a six month contract.</p>
        </section>
        <section class="elementor-top-section">
          <h2 class="elementor-heading-title">You are/have:</h2>
          <ul><li>Strong Python skills</li></ul>
        </section>
        <section class="elementor-top-section">
          <h2 class="elementor-heading-title">You will:</h2>
          <ul><li>Build scheduling services</li></ul>
        </section>
        <section class="elementor-top-section">
          <h2 class="elementor-heading-title">Compensation</h2>
          <p>IDR 10,000,000 - 15,000,000 per month</p>
        </section>
      </main>
    """
    job = posting(
        "StaffAny",
        url="https://www.staffany.com/careers-backend-engineer/",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == job.url
        return httpx.Response(200, text=body)

    details = StaffanyScraper(client=client(handler)).extract_job_details(job)

    assert "About the role\nThis is a remote position" in details.description_text
    assert "You are/have:\nStrong Python skills" in details.description_text
    assert "You will:\nBuild scheduling services" in details.description_text
    assert details.work_arrangement == "Remote"
    assert details.employment_type == "Contract"
    assert details.salary == "IDR 10,000,000 - 15,000,000 per month"


@pytest.mark.parametrize(
    "body",
    [
        "<html><body>Unexpected page</body></html>",
        (
            '<section class="elementor-top-section"><h2>About the role</h2>'
            "<p>Only an introduction.</p></section>"
        ),
    ],
)
def test_staffany_rejects_missing_role_or_requirement_sections(body: str) -> None:
    scraper = StaffanyScraper(
        client=client(lambda request: httpx.Response(200, text=body))
    )

    with pytest.raises(JobDetailStructureChangedError, match="StaffAny"):
        scraper.extract_job_details(posting("StaffAny"))


def test_amartha_fetches_workable_markdown_and_removes_apply_boilerplate() -> None:
    markdown = """# Principal Backend Engineer

> Amartha · South Jakarta, Indonesia · Full-time · Posted 2026-03-12

**Workplace:** on_site

## Description

Design and operate highly scalable services.

## Requirements

- Five years of backend experience
- Strong Go skills

## Apply

[Apply at Amartha](https://example.test/apply)

---
Powered by Workable
"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/amartha/jobs/view/201EF9FE4A.md"
        assert request.headers["accept"] == "text/markdown"
        return httpx.Response(200, text=markdown)

    details = AmarthaScraper(client=client(handler)).extract_job_details(
        posting("Amartha", job_id="201EF9FE4A")
    )

    assert details.description_text == (
        "Description\nDesign and operate highly scalable services.\n\n"
        "Requirements\n- Five years of backend experience\n- Strong Go skills"
    )
    assert "Apply at Amartha" not in details.description_text
    assert details.location == "South Jakarta, Indonesia"
    assert details.work_arrangement == "on_site"
    assert details.employment_type == "Full-time"


@pytest.mark.parametrize(
    "markdown",
    [
        "# Backend Engineer\n\n## Description\n\nOnly a description.",
        "# Backend Engineer\n\n## Requirements\n\nOnly requirements.",
        "# Backend Engineer\n\n## Description\n\n\n## Requirements\n\n",
    ],
)
def test_amartha_rejects_incomplete_workable_markdown(markdown: str) -> None:
    scraper = AmarthaScraper(
        client=client(lambda request: httpx.Response(200, text=markdown))
    )

    with pytest.raises(JobDetailStructureChangedError, match="Amartha"):
        scraper.extract_job_details(posting("Amartha", job_id="201EF9FE4A"))
