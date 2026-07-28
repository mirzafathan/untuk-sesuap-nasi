import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import HTMLScraper
from scrapers.utils import absolute_url, id_from_url, matching_jobs, required_attr, required_text


class StockbitScraper(HTMLScraper):
    company = "Stockbit"
    base_url = "https://careers.stockbit.com"
    api_url = "https://careers.stockbit.com/api/v2/jobs"

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        response = self.client.get(
            f"{self.api_url}/{job.id}",
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise JobDetailStructureChangedError(
                f"{self.company}: detail response is not JSON"
            ) from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
            or not isinstance(data, dict)
        ):
            raise JobDetailStructureChangedError(
                f"{self.company}: invalid detail API response envelope"
            )
        raw_id = data.get("id")
        description_html = data.get("description")
        if raw_id in (None, "") or str(raw_id) != job.id:
            raise JobDetailStructureChangedError(
                f"{self.company}: detail API returned the wrong job ID"
            )
        if not isinstance(description_html, str) or not description_html.strip():
            raise JobDetailStructureChangedError(
                f"{self.company}: detail API description is missing"
            )
        description = self._clean_description_html(description_html)
        if not description:
            raise JobDetailStructureChangedError(
                f"{self.company}: detail API description is empty"
            )

        city = self._optional_detail_text(data, "city")
        region = self._optional_detail_text(data, "region")
        location_parts = [city] if city else []
        if region and (not city or region.casefold() != city.casefold()):
            location_parts.append(region)

        return JobDetails(
            description_text=description,
            location=", ".join(location_parts) or None,
            employment_type=self._optional_detail_text(data, "employment-type"),
            salary=self._optional_detail_text(data, "salary"),
        )

    def _optional_detail_text(self, data: dict, key: str) -> str | None:
        value = data.get(key)
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise JobDetailStructureChangedError(
                f"{self.company}: detail {key} field changed type"
            )
        return self._clean_inline(value) or None

    @staticmethod
    def _clean_description_html(value: str) -> str:
        soup = BeautifulSoup(value, "html.parser")
        for line_break in soup.find_all("br"):
            line_break.replace_with("\n")
        for item in soup.find_all("li"):
            item_text = StockbitScraper._clean_inline(item.get_text(" ", strip=True))
            item.clear()
            item.append(f"- {item_text}" if item_text else "")
        return "\n".join(
            line
            for raw_line in soup.get_text("\n", strip=True).splitlines()
            if (line := StockbitScraper._clean_inline(raw_line))
        )

    @staticmethod
    def _clean_inline(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    def extract_jobs(self, url: str) -> list[JobPosting]:
        search = parse_qs(urlparse(url).query).get("search", [""])[0]
        page = 1
        last_page: int | None = None
        advertised_total: int | None = None
        raw_ids: set[str] = set()
        jobs: list[JobPosting] = []
        while last_page is None or page <= last_page:
            payload = self._json(
                self.client.get(
                    self.api_url,
                    params={"search": search, "page": page},
                    headers={"Accept": "application/json"},
                )
            )
            records, page_count, record_count = self._page_fields(payload)
            if last_page is not None and (
                page_count != last_page or record_count != advertised_total
            ):
                raise DOMStructureChangedError(
                    f"{self.company}: pagination totals changed while crawling"
                )
            last_page, advertised_total = page_count, record_count
            for record in records:
                raw_id = record.get("id") if isinstance(record, dict) else None
                if raw_id in (None, ""):
                    raise DOMStructureChangedError(
                        f"{self.company}: API job lacks an ID"
                    )
                raw_ids.add(str(raw_id))
            jobs.extend(self.parse_api_payload(payload))
            if page < last_page and not records:
                raise DOMStructureChangedError(
                    f"{self.company}: pagination returned no jobs before the last page"
                )
            page += 1

        if advertised_total is None or len(raw_ids) != advertised_total:
            raise DOMStructureChangedError(
                f"{self.company}: loaded {len(raw_ids)} unique jobs but API advertised "
                f"{advertised_total}"
            )
        return jobs

    def _page_fields(self, payload: dict) -> tuple[list, int, int]:
        records, meta = payload.get("data"), payload.get("meta")
        if payload.get("success") is not True or not isinstance(records, list):
            raise DOMStructureChangedError(f"{self.company}: invalid API response envelope")
        if not isinstance(meta, dict):
            raise DOMStructureChangedError(f"{self.company}: pagination metadata is missing")
        page_count, record_count = meta.get("page-count"), meta.get("record-count")
        if (
            not isinstance(page_count, int)
            or page_count < 0
            or not isinstance(record_count, int)
            or record_count < 0
        ):
            raise DOMStructureChangedError(f"{self.company}: pagination metadata changed")
        return records, page_count, record_count

    def parse_api_payload(self, payload: dict) -> list[JobPosting]:
        records, _, _ = self._page_fields(payload)
        jobs: list[JobPosting] = []
        for record in records:
            if not isinstance(record, dict):
                raise DOMStructureChangedError(f"{self.company}: malformed API job")
            raw_id, title = record.get("id"), record.get("title")
            if raw_id in (None, "") or not isinstance(title, str) or not title.strip():
                raise DOMStructureChangedError(f"{self.company}: API job lacks ID or title")
            created = record.get("createdAt")
            jobs.append(
                JobPosting(
                    id=str(raw_id),
                    title=title.strip(),
                    company=self.company,
                    url=f"{self.base_url}/jobs/{raw_id}",
                    posted_date=created if isinstance(created, str) and created else None,
                )
            )
        return matching_jobs(jobs)

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("ul.jobs-list > li")
        if not cards:
            raise DOMStructureChangedError(f"{self.company}: no job cards found")

        jobs: list[JobPosting] = []
        for card in cards:
            title = required_text(card, ".jobs-card > p", self.company)
            href = required_attr(card, 'a[href^="/jobs/"]', "href", self.company)
            job_url = absolute_url(self.base_url, href)
            jobs.append(
                JobPosting(
                    id=id_from_url(job_url, self.company),
                    title=title,
                    company=self.company,
                    url=job_url,
                )
            )
        return matching_jobs(jobs)
