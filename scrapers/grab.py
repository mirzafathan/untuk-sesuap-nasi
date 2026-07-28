import json
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import HTMLScraper
from scrapers.utils import matching_jobs, required_attr, required_text


class GrabScraper(HTMLScraper):
    company = "Grab"
    browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        response = self.client.get(job.url, headers=self.browser_headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        schema_node = soup.select_one("script#js-job-posting")
        raw_schema = schema_node.string if schema_node is not None else None
        if not isinstance(raw_schema, str) or not raw_schema.strip():
            raise JobDetailStructureChangedError(
                f"{self.company}: structured job detail is missing"
            )
        try:
            schema = json.loads(raw_schema)
        except (TypeError, ValueError) as exc:
            raise JobDetailStructureChangedError(
                f"{self.company}: structured job detail is not valid JSON"
            ) from exc
        if not isinstance(schema, dict) or schema.get("@type") != "JobPosting":
            raise JobDetailStructureChangedError(
                f"{self.company}: structured job detail schema changed"
            )

        description = schema.get("description")
        if not isinstance(description, str):
            raise JobDetailStructureChangedError(
                f"{self.company}: structured job description is missing"
            )
        description_text = self._html_text(description)
        if not description_text:
            raise JobDetailStructureChangedError(
                f"{self.company}: structured job description is empty"
            )

        return JobDetails(
            description_text=description_text,
            location=self._schema_location(schema.get("jobLocation")),
            work_arrangement=self._work_arrangement(schema.get("jobLocationType")),
            employment_type=self._employment_type(schema.get("employmentType")),
            salary=self._salary(schema.get("baseSalary")),
        )

    @staticmethod
    def _html_text(value: str) -> str:
        soup = BeautifulSoup(value, "html.parser")
        return "\n".join(
            " ".join(part.split()) for part in soup.stripped_strings if part.strip()
        )

    def _schema_location(self, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, list):
            locations = [self._schema_location(item) for item in value]
            return "; ".join(location for location in locations if location) or None
        if not isinstance(value, dict):
            raise JobDetailStructureChangedError(
                f"{self.company}: structured job location changed"
            )

        name = value.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        address = value.get("address")
        if address is None:
            return None
        if not isinstance(address, dict):
            raise JobDetailStructureChangedError(
                f"{self.company}: structured job address changed"
            )
        parts: list[str] = []
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            part = address.get(key)
            if isinstance(part, dict):
                part = part.get("name")
            if isinstance(part, str) and part.strip() and part.strip() not in parts:
                parts.append(part.strip())
        return ", ".join(parts) or None

    def _employment_type(self, value: object) -> str | None:
        if value is None:
            return None
        values = value if isinstance(value, list) else [value]
        if not values or not all(isinstance(item, str) and item.strip() for item in values):
            raise JobDetailStructureChangedError(
                f"{self.company}: structured employment type changed"
            )
        return ", ".join(self._display_enum(item) for item in values)

    def _work_arrangement(self, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise JobDetailStructureChangedError(
                f"{self.company}: structured work arrangement changed"
            )
        if value.strip().upper() == "TELECOMMUTE":
            return "Remote"
        return self._display_enum(value)

    @staticmethod
    def _display_enum(value: str) -> str:
        normalized = value.strip().upper()
        known = {
            "FULL_TIME": "Full-time",
            "PART_TIME": "Part-time",
            "CONTRACTOR": "Contract",
            "TEMPORARY": "Temporary",
            "INTERN": "Internship",
            "VOLUNTEER": "Volunteer",
            "PER_DIEM": "Per diem",
            "OTHER": "Other",
        }
        return known.get(normalized, value.replace("_", " ").strip().title())

    def _salary(self, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        if not isinstance(value, dict):
            raise JobDetailStructureChangedError(
                f"{self.company}: structured salary changed"
            )

        currency = value.get("currency")
        currency_text = currency.strip() if isinstance(currency, str) else ""
        salary_value = value.get("value")
        if isinstance(salary_value, (int, float)) and not isinstance(salary_value, bool):
            amount = self._format_number(salary_value)
            return " ".join(part for part in (currency_text, amount) if part)
        if not isinstance(salary_value, dict):
            return None

        exact = salary_value.get("value")
        minimum = salary_value.get("minValue")
        maximum = salary_value.get("maxValue")
        amount: str | None = None
        if self._is_number(exact):
            amount = self._format_number(exact)
        elif self._is_number(minimum) and self._is_number(maximum):
            amount = f"{self._format_number(minimum)}–{self._format_number(maximum)}"
        elif self._is_number(minimum):
            amount = f"from {self._format_number(minimum)}"
        elif self._is_number(maximum):
            amount = f"up to {self._format_number(maximum)}"
        if amount is None:
            return None

        result = " ".join(part for part in (currency_text, amount) if part)
        unit = salary_value.get("unitText")
        if isinstance(unit, str) and unit.strip():
            result = f"{result} per {unit.strip().replace('_', ' ').lower()}"
        return result

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @staticmethod
    def _format_number(value: int | float) -> str:
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}".rstrip("0").rstrip(".")

    def extract_jobs(self, url: str) -> list[JobPosting]:
        current_url: str | None = url
        visited: set[str] = set()
        raw_ids: set[str] = set()
        advertised_total: int | None = None
        jobs: list[JobPosting] = []

        while current_url:
            if current_url in visited:
                raise DOMStructureChangedError(
                    f"{self.company}: pagination repeated a page before completion"
                )
            visited.add(current_url)
            response = self.client.get(current_url, headers=self.browser_headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            cards, page_total = self._page_fields(soup)
            if advertised_total is not None and page_total != advertised_total:
                raise DOMStructureChangedError(
                    f"{self.company}: advertised result count changed during pagination"
                )
            advertised_total = page_total
            for card in cards:
                raw_id = card.get("data-id")
                if not isinstance(raw_id, str) or not raw_id:
                    raise DOMStructureChangedError(f"{self.company}: job card lacks an ID")
                raw_ids.add(raw_id)
            jobs.extend(self.parse_html(response.text, str(response.url)))

            next_link = soup.select_one(
                'ul.pagination a[rel~="next"][href], '
                'ul.pagination a[aria-label="Next page"][href]'
            )
            next_href = next_link.get("href") if next_link else None
            current_url = (
                urljoin(str(response.url), next_href)
                if isinstance(next_href, str) and next_href
                else None
            )

        if advertised_total is None or len(raw_ids) != advertised_total:
            raise DOMStructureChangedError(
                f"{self.company}: loaded {len(raw_ids)} unique jobs but advertised "
                f"{advertised_total}"
            )
        return jobs

    def _page_fields(self, soup: BeautifulSoup) -> tuple[list, int]:
        count_node = soup.select_one(".job-count")
        count_match = re.search(
            r"\bof\s+([\d,]+)\s+matching\s+jobs\b",
            count_node.get_text(" ", strip=True) if count_node else "",
            re.IGNORECASE,
        )
        if count_match is None:
            raise DOMStructureChangedError(
                f"{self.company}: advertised matching-job count is missing"
            )
        total = int(count_match.group(1).replace(",", ""))
        cards = soup.select(".card-job")
        if not cards and total:
            raise DOMStructureChangedError(
                f"{self.company}: nonzero count has no job cards"
            )
        return cards, total

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        cards, total = self._page_fields(soup)
        if not cards and total == 0:
            return []

        jobs: list[JobPosting] = []
        for card in cards:
            raw_id = card.get("data-id")
            if not isinstance(raw_id, str) or not raw_id:
                raise DOMStructureChangedError(f"{self.company}: job card lacks an ID")
            title = required_text(card, ".card-title", self.company)
            href = required_attr(card, ".card-title a[href]", "href", self.company)
            jobs.append(
                JobPosting(
                    id=raw_id,
                    title=title,
                    company=self.company,
                    url=urljoin(source_url, href),
                )
            )
        return matching_jobs(jobs)
