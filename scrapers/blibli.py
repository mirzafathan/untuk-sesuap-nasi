from urllib.parse import quote, quote_plus

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import BaseScraper
from scrapers.utils import matching_jobs, required_text, slugify


class BlibliScraper(BaseScraper):
    company = "Blibli"
    api_url = "https://careers.blibli.com/ext/api/fetch-jobs"
    detail_api_base = "https://careers.blibli.com/ext/api/job/"

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        response = self.client.get(
            f"{self.detail_api_base}{quote(job.id, safe='')}",
            headers={"Accept": "application/json"},
        )
        try:
            payload = self._json(response)
        except DOMStructureChangedError as exc:
            raise JobDetailStructureChangedError(
                f"{self.company}: expected a JSON job-detail response"
            ) from exc

        status = payload.get("status")
        record = payload.get("responseObject")
        if (
            not isinstance(status, dict)
            or status.get("code") != 1000
            or not isinstance(record, dict)
        ):
            raise JobDetailStructureChangedError(
                f"{self.company}: invalid job-detail response envelope"
            )
        if record.get("jobCode") != job.id:
            raise JobDetailStructureChangedError(
                f"{self.company}: detail response has a missing or mismatched job code"
            )
        description = _html_to_text(record.get("jobSummary"))
        if not description:
            raise JobDetailStructureChangedError(
                f"{self.company}: jobSummary is missing or empty"
            )
        return JobDetails(
            description_text=description,
            location=_optional_text(record.get("location")),
            employment_type=_optional_text(record.get("employmentType")),
        )

    def extract_jobs(self, url: str) -> list[JobPosting]:
        jobs: list[JobPosting] = []
        token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            response = self.client.get(
                self.api_url,
                params={"paginationToken": token} if token is not None else None,
                headers={"Accept": "application/json"},
            )
            payload = self._json(response)
            jobs.extend(self.parse_api_payload(payload))
            if "paginationToken" not in payload:
                raise DOMStructureChangedError(
                    f"{self.company}: paginationToken is missing"
                )
            next_token = payload["paginationToken"]
            if next_token in (None, ""):
                return jobs
            if not isinstance(next_token, str):
                raise DOMStructureChangedError(
                    f"{self.company}: paginationToken has an invalid type"
                )
            if next_token in seen_tokens:
                raise DOMStructureChangedError(
                    f"{self.company}: pagination token repeated before completion"
                )
            seen_tokens.add(next_token)
            token = next_token

    def parse_api_payload(self, payload: dict) -> list[JobPosting]:
        records = payload.get("responseObject")
        if not isinstance(records, list):
            raise DOMStructureChangedError(f"{self.company}: responseObject is missing")
        jobs: list[JobPosting] = []
        for record in records:
            if not isinstance(record, dict):
                raise DOMStructureChangedError(f"{self.company}: malformed API job")
            if not (
                record.get("active") is True
                and record.get("postingStatus") is True
                and record.get("recruitmentStatus") == "Open"
            ):
                continue
            raw_id, title = record.get("jobCode"), record.get("jobName")
            if not isinstance(raw_id, str) or not raw_id or not isinstance(title, str) or not title:
                raise DOMStructureChangedError(f"{self.company}: active job lacks ID or title")
            job_url = (
                f"https://careers.blibli.com/job-detail/{slugify(title)}"
                f"?job={quote_plus(raw_id)}"
            )
            jobs.append(
                JobPosting(id=raw_id, title=title.strip(), company=self.company, url=job_url)
            )
        return matching_jobs(jobs)

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".job__card")
        if not cards:
            raise DOMStructureChangedError(f"{self.company}: no job cards found")
        for card in cards:
            required_text(card, ".job__title", self.company)
            if card.select_one("a[href]") is None:
                raise DOMStructureChangedError(f"{self.company}: job card has no detail URL")
        raise DOMStructureChangedError(f"{self.company}: unsupported legacy job-card structure")


def _html_to_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for node in soup.select("script, style"):
        node.decompose()
    return "\n".join(
        " ".join(line.split())
        for line in soup.get_text("\n", strip=True).splitlines()
        if line.strip()
    )


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
