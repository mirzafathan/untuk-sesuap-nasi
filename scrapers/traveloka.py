import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import BaseScraper
from scrapers.utils import absolute_url, id_from_url, matching_jobs


class TravelokaScraper(BaseScraper):
    company = "Traveloka"
    api_url = "https://careers-api.mte.traveloka.com/career/jobs/search"
    careers_base = "https://careers.traveloka.com"

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        response = self.client.get(job.url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        heading = next(
            (
                node
                for node in soup.find_all("h1")
                if node.get_text(" ", strip=True).casefold() == "job description"
            ),
            None,
        )
        detail_node = heading.find_next_sibling() if heading is not None else None
        if not isinstance(detail_node, Tag):
            raise JobDetailStructureChangedError(
                f"{self.company}: Job Description container is missing"
            )
        detail_labels = {
            self._clean_inline(node.get_text(" ", strip=True)).casefold()
            for node in detail_node.find_all("strong")
        }
        missing_labels = {"responsibilities", "requirements"} - detail_labels
        if missing_labels:
            raise JobDetailStructureChangedError(
                f"{self.company}: detail sections are missing: "
                f"{', '.join(sorted(missing_labels))}"
            )
        description = self._clean_detail_node(detail_node)
        if not description:
            raise JobDetailStructureChangedError(
                f"{self.company}: Job Description container is empty"
            )

        return JobDetails(
            description_text=description,
            location=self._metadata_value(soup, "Location"),
            employment_type=self._metadata_value(soup, "Job Type"),
        )

    @staticmethod
    def _metadata_value(soup: BeautifulSoup, label: str) -> str | None:
        for node in soup.find_all("span"):
            node_text = TravelokaScraper._clean_inline(node.get_text(" ", strip=True))
            if node_text.casefold() != label.casefold():
                continue
            for value in node.parent.stripped_strings:
                candidate = TravelokaScraper._clean_inline(value)
                if candidate and candidate.casefold() != label.casefold():
                    return candidate
        return None

    @staticmethod
    def _clean_detail_node(node: Tag) -> str:
        for line_break in node.find_all("br"):
            line_break.replace_with("\n")
        for item in node.find_all("li"):
            item_text = TravelokaScraper._clean_inline(item.get_text(" ", strip=True))
            item.clear()
            item.append(f"- {item_text}" if item_text else "")
        return "\n".join(
            line
            for raw_line in node.get_text("\n", strip=True).splitlines()
            if (line := TravelokaScraper._clean_inline(raw_line))
        )

    @staticmethod
    def _clean_inline(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    def extract_jobs(self, url: str) -> list[JobPosting]:
        keyword = parse_qs(urlparse(url).query).get("keyword", [""])[0]
        page = 1
        jobs: list[JobPosting] = []
        raw_ids: set[str] = set()
        advertised_total: int | None = None
        expected_last_page: int | None = None
        while True:
            payload = self._json(
                self.client.get(
                    self.api_url,
                    params={"keyword": keyword, "page": page, "limit": 100},
                )
            )
            jobs.extend(self.parse_api_payload(payload))
            data = payload.get("data")
            last_page = data.get("lastPage") if isinstance(data, dict) else None
            records = data.get("jobs") if isinstance(data, dict) else None
            response_page = data.get("page") if isinstance(data, dict) else None
            total = data.get("total") if isinstance(data, dict) else None
            if not isinstance(last_page, int) or not isinstance(records, list):
                raise DOMStructureChangedError(f"{self.company}: pagination fields changed")
            if response_page is not None and response_page != page:
                raise DOMStructureChangedError(
                    f"{self.company}: API returned the wrong page"
                )
            if expected_last_page is not None and last_page != expected_last_page:
                raise DOMStructureChangedError(
                    f"{self.company}: lastPage changed during pagination"
                )
            expected_last_page = last_page
            if total is not None:
                if not isinstance(total, int) or total < 0:
                    raise DOMStructureChangedError(f"{self.company}: total field changed")
                if advertised_total is not None and total != advertised_total:
                    raise DOMStructureChangedError(
                        f"{self.company}: result total changed during pagination"
                    )
                advertised_total = total
            for record in records:
                raw_id = record.get("requisitionId") if isinstance(record, dict) else None
                if not isinstance(raw_id, str) or not raw_id:
                    raise DOMStructureChangedError(f"{self.company}: API job lacks an ID")
                raw_ids.add(raw_id)
            if page >= last_page:
                break
            if not records:
                raise DOMStructureChangedError(f"{self.company}: pagination returned no jobs")
            page += 1
        if advertised_total is not None and len(raw_ids) != advertised_total:
            raise DOMStructureChangedError(
                f"{self.company}: loaded {len(raw_ids)} unique jobs but API advertised "
                f"{advertised_total}"
            )
        return jobs

    def parse_api_payload(self, payload: dict) -> list[JobPosting]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DOMStructureChangedError(f"{self.company}: API data field is missing")
        records, last_page = data.get("jobs"), data.get("lastPage")
        if not isinstance(records, list) or not isinstance(last_page, int):
            raise DOMStructureChangedError(f"{self.company}: API job fields changed")
        jobs: list[JobPosting] = []
        for record in records:
            if not isinstance(record, dict):
                raise DOMStructureChangedError(f"{self.company}: malformed API job")
            raw_id, title, link = (
                record.get("requisitionId"),
                record.get("title"),
                record.get("link"),
            )
            if not all(isinstance(value, str) and value for value in (raw_id, title, link)):
                raise DOMStructureChangedError(f"{self.company}: API job lacks required fields")
            timestamp = record.get("createdAt")
            posted_date = None
            if isinstance(timestamp, (int, float)):
                posted_date = datetime.fromtimestamp(
                    timestamp / 1000, tz=timezone.utc
                ).date().isoformat()
            jobs.append(
                JobPosting(
                    id=raw_id,
                    title=title.strip(),
                    company=self.company,
                    url=absolute_url(self.careers_base, link),
                    posted_date=posted_date,
                )
            )
        return matching_jobs(jobs)

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select('div.list a[href^="/jobs/"]')
        if not cards:
            raise DOMStructureChangedError(f"{self.company}: job links are missing")
        jobs: list[JobPosting] = []
        for card in cards:
            strings = list(card.stripped_strings)
            if not strings:
                raise DOMStructureChangedError(f"{self.company}: job title is missing")
            job_url = absolute_url(self.careers_base, card.get("href", ""))
            posted_date = next(
                (text for text in strings[1:] if re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", text)),
                None,
            )
            jobs.append(
                JobPosting(
                    id=id_from_url(job_url, self.company).split("-", 1)[0],
                    title=strings[0],
                    company=self.company,
                    url=job_url,
                    posted_date=posted_date,
                )
            )
        return matching_jobs(jobs)
