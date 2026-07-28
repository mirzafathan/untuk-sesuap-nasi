from abc import ABC, abstractmethod
from typing import Any

import httpx

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting


DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/json",
    "User-Agent": "daily-engineering-job-crawler/1.0",
}


class BaseScraper(ABC):
    company: str

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=30.0,
        )
        self._owns_client = client is None

    @abstractmethod
    def extract_jobs(self, url: str) -> list[JobPosting]:
        """Fetch and return matching job postings from a career source."""

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        raise JobDetailStructureChangedError(
            f"{self.company}: job-detail extraction is not implemented"
        )

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise DOMStructureChangedError(
                f"{self.company}: expected a JSON job response"
            ) from exc
        if not isinstance(payload, dict):
            raise DOMStructureChangedError(f"{self.company}: job response is not an object")
        return payload


class HTMLScraper(BaseScraper, ABC):
    def extract_jobs(self, url: str) -> list[JobPosting]:
        response = self.client.get(url)
        response.raise_for_status()
        return self.parse_html(response.text, str(response.url))

    @abstractmethod
    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        """Parse a rendered outerHTML document or fragment."""
