import re

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import BaseScraper
from scrapers.utils import matching_jobs


class DanaScraper(BaseScraper):
    company = "DANA"
    api_url = "https://api.career.dana.id/api/career/jobs"
    detail_api_url = "https://api.career.dana.id/api/career/job"

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        response = self.client.get(f"{self.detail_api_url}/{job.id}")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise JobDetailStructureChangedError(
                f"{self.company}: detail response is not JSON"
            ) from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        record = data.get("job") if isinstance(data, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("status") != 200
            or not isinstance(record, dict)
        ):
            raise JobDetailStructureChangedError(
                f"{self.company}: invalid detail API response envelope"
            )
        if record.get("jobId") != job.id:
            raise JobDetailStructureChangedError(
                f"{self.company}: detail API returned the wrong job ID"
            )

        description = record.get("description")
        if not isinstance(description, str) or not description.strip():
            raise JobDetailStructureChangedError(
                f"{self.company}: job description is missing"
            )
        responsibilities = self._detail_items(record, "responsibilities")
        requirements = self._detail_items(record, "requirements")
        sections = [description.strip()]
        if responsibilities:
            sections.append(
                "Responsibilities\n" + "\n".join(f"- {item}" for item in responsibilities)
            )
        if requirements:
            sections.append(
                "Requirements\n" + "\n".join(f"- {item}" for item in requirements)
            )

        employment_type = self._optional_detail_text(record, "employmentType")
        if employment_type:
            normalized_type = employment_type.replace("_", " ").casefold()
            employment_type = {
                "full time": "Full-time",
                "part time": "Part-time",
            }.get(normalized_type, employment_type)

        return JobDetails(
            description_text="\n\n".join(sections),
            location=self._optional_detail_text(record, "location"),
            employment_type=employment_type,
        )

    def _detail_items(self, record: dict, key: str) -> list[str]:
        values = record.get(key)
        if not isinstance(values, list):
            raise JobDetailStructureChangedError(
                f"{self.company}: detail {key} field is not a list"
            )
        cleaned: list[str] = []
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise JobDetailStructureChangedError(
                    f"{self.company}: detail {key} contains an invalid item"
                )
            cleaned.append(value.strip())
        return cleaned

    def _optional_detail_text(self, record: dict, key: str) -> str | None:
        value = record.get(key)
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise JobDetailStructureChangedError(
                f"{self.company}: detail {key} field changed type"
            )
        return value.strip() or None

    def extract_jobs(self, url: str) -> list[JobPosting]:
        return self.parse_api_payload(self._json(self.client.get(self.api_url)))

    def parse_api_payload(self, payload: dict) -> list[JobPosting]:
        departments = payload.get("data")
        if payload.get("status") != 200 or not isinstance(departments, list):
            raise DOMStructureChangedError(f"{self.company}: API response fields changed")
        jobs: list[JobPosting] = []
        for department in departments:
            records = department.get("jobs") if isinstance(department, dict) else None
            if not isinstance(records, list):
                raise DOMStructureChangedError(f"{self.company}: department jobs are missing")
            for record in records:
                if not isinstance(record, dict):
                    raise DOMStructureChangedError(f"{self.company}: malformed API job")
                raw_id, title = record.get("jobId"), record.get("title")
                if not isinstance(raw_id, str) or not raw_id or not isinstance(title, str) or not title:
                    raise DOMStructureChangedError(f"{self.company}: API job lacks ID or title")
                jobs.append(
                    JobPosting(
                        id=raw_id,
                        title=title.strip(),
                        company=self.company,
                        url=f"https://www.career.dana.id/jobs/{raw_id}",
                    )
                )
        return matching_jobs(jobs)

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        root = soup.select_one("section#job-list")
        if root is None:
            raise DOMStructureChangedError(f"{self.company}: #job-list is missing")
        links = root.select('a[href*="/jobs/"]')
        if not links:
            text = root.get_text(" ", strip=True)
            match = re.search(r"(\d+)\s+jobs?\s+available", text, re.I)
            if match and int(match.group(1)) == 0:
                return []
            raise DOMStructureChangedError(
                f"{self.company}: job count is nonzero but individual job nodes are absent"
            )
        raise DOMStructureChangedError(f"{self.company}: unrecognized individual job structure")
