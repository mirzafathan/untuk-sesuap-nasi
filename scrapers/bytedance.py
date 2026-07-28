import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import BaseScraper
from scrapers.utils import id_from_url, matching_jobs, required_text


class ByteDanceScraper(BaseScraper):
    company = "ByteDance"
    api_url = "https://jobs.bytedance.com/api/v1/public/supplier/search/job/posts"
    page_size = 1000
    max_collection_passes = 3
    api_headers = {
        "Content-Type": "application/json",
        "accept-language": "en-US",
        "website-path": "en",
        "origin": "https://joinbytedance.com",
    }

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        response = self.client.get(job.url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        responsibilities = self._detail_section(soup, "Responsibilities")
        qualifications = self._detail_section(soup, "Qualifications")
        description = (
            f"Responsibilities\n{responsibilities}\n\n"
            f"Qualifications\n{qualifications}"
        )
        job_information = self._optional_detail_section(soup, "Job Information")
        salary = None
        if job_information:
            match = re.search(
                r"base salary range[^.\n]*?\bis\s+([^\n.]+?(?:annually|per year|yearly))",
                job_information,
                re.IGNORECASE,
            )
            if match:
                salary = match.group(1).strip()

        return JobDetails(
            description_text=description,
            location=self._metadata_value(soup, "Location"),
            work_arrangement=(
                self._metadata_value(soup, "Work Arrangement")
                or self._metadata_value(soup, "Workplace Type")
            ),
            employment_type=self._metadata_value(soup, "Employment Type"),
            salary=salary,
        )

    def _detail_section(self, soup: BeautifulSoup, heading: str) -> str:
        value = self._optional_detail_section(soup, heading)
        if not value:
            raise JobDetailStructureChangedError(
                f"{self.company}: {heading} detail section is missing or empty"
            )
        return value

    @staticmethod
    def _optional_detail_section(soup: BeautifulSoup, heading: str) -> str | None:
        heading_node = next(
            (
                node
                for node in soup.select(".bd-title")
                if node.get_text(" ", strip=True).casefold() == heading.casefold()
            ),
            None,
        )
        if heading_node is None:
            return None
        siblings = [
            ByteDanceScraper._clean_text(node.get_text("\n", strip=True))
            for node in heading_node.next_siblings
            if isinstance(node, Tag)
        ]
        value = "\n".join(part for part in siblings if part)
        return value or None

    @staticmethod
    def _metadata_value(soup: BeautifulSoup, label: str) -> str | None:
        for node in soup.find_all(("p", "span")):
            node_text = ByteDanceScraper._clean_inline(node.get_text(" ", strip=True))
            if node_text.rstrip(":").strip().casefold() != label.casefold():
                continue
            for value in node.parent.stripped_strings:
                candidate = ByteDanceScraper._clean_inline(value)
                if (
                    not candidate
                    or candidate.strip(":").strip().casefold() == label.casefold()
                    or not candidate.strip(":").strip()
                ):
                    continue
                return candidate
        return None

    @staticmethod
    def _clean_inline(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _clean_text(value: str) -> str:
        return "\n".join(
            line
            for raw_line in value.splitlines()
            if (line := ByteDanceScraper._clean_inline(raw_line))
        )

    def extract_jobs(self, url: str) -> list[JobPosting]:
        query = parse_qs(urlparse(url).query)
        jobs_by_id: dict[str, JobPosting] = {}
        raw_ids: set[str] = set()
        advertised_total: int | None = None
        for _ in range(self.max_collection_passes):
            offset = 0
            while True:
                body = {
                    "keyword": query.get("keyword", [""])[0],
                    "limit": self.page_size,
                    "offset": offset,
                    "recruitment_id_list": [],
                    "job_category_id_list": [],
                    "subject_id_list": [],
                    "location_code_list": [],
                    "tag_id_list": [],
                }
                payload = self._json(
                    self.client.post(self.api_url, json=body, headers=self.api_headers)
                )
                page_jobs = self.parse_api_payload(payload)
                for page_job in page_jobs:
                    jobs_by_id.setdefault(page_job.id, page_job)

                data = payload.get("data")
                records = data.get("job_post_list") if isinstance(data, dict) else None
                total = data.get("count") if isinstance(data, dict) else None
                if (
                    not isinstance(records, list)
                    or not isinstance(total, int)
                    or total < 0
                ):
                    raise DOMStructureChangedError(
                        f"{self.company}: pagination fields changed"
                    )
                if advertised_total is not None and total != advertised_total:
                    raise DOMStructureChangedError(
                        f"{self.company}: result count changed during pagination"
                    )
                advertised_total = total
                for record in records:
                    raw_id = record.get("id") if isinstance(record, dict) else None
                    if not isinstance(raw_id, str) or not raw_id:
                        raise DOMStructureChangedError(
                            f"{self.company}: API job lacks an ID"
                        )
                    raw_ids.add(raw_id)

                offset += len(records)
                if offset >= total or not records:
                    break

            if len(raw_ids) == advertised_total:
                return list(jobs_by_id.values())
            if advertised_total is not None and len(raw_ids) > advertised_total:
                break

        raise DOMStructureChangedError(
            f"{self.company}: loaded {len(raw_ids)} unique jobs but API advertised "
            f"{advertised_total} after {self.max_collection_passes} passes"
        )

    def parse_api_payload(self, payload: dict) -> list[JobPosting]:
        data = payload.get("data")
        if payload.get("code") != 0 or not isinstance(data, dict):
            raise DOMStructureChangedError(f"{self.company}: invalid API response envelope")
        records, total = data.get("job_post_list"), data.get("count")
        if not isinstance(records, list) or not isinstance(total, int):
            raise DOMStructureChangedError(f"{self.company}: job fields changed")
        jobs: list[JobPosting] = []
        for record in records:
            if not isinstance(record, dict):
                raise DOMStructureChangedError(f"{self.company}: malformed API job")
            raw_id, title = record.get("id"), record.get("title")
            if not isinstance(raw_id, str) or not raw_id or not isinstance(title, str) or not title:
                raise DOMStructureChangedError(f"{self.company}: API job lacks ID or title")
            jobs.append(
                JobPosting(
                    id=raw_id,
                    title=title.strip(),
                    company=self.company,
                    url=f"https://joinbytedance.com/search/{raw_id}",
                )
            )
        return matching_jobs(jobs)

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select('a[href^="https://joinbytedance.com/search/"]')
        if not cards:
            raise DOMStructureChangedError(f"{self.company}: no result links found")
        jobs: list[JobPosting] = []
        for card in cards:
            title = required_text(card, ".bd-title", self.company)
            job_url = card.get("href", "")
            jobs.append(
                JobPosting(
                    id=id_from_url(job_url, self.company),
                    title=title,
                    company=self.company,
                    url=job_url,
                )
            )
        return matching_jobs(jobs)
