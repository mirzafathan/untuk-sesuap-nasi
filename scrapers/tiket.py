import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import BaseScraper
from scrapers.utils import absolute_url, matching_jobs, required_attr, required_text


class TiketScraper(BaseScraper):
    company = "Tiket"
    api_url = "https://tiketdotcom.wd3.myworkdayjobs.com/wday/cxs/tiketdotcom/Tiket_Careers/jobs"
    careers_base = "https://tiketdotcom.wd3.myworkdayjobs.com/Tiket_Careers"
    detail_api_base = (
        "https://tiketdotcom.wd3.myworkdayjobs.com/wday/cxs/tiketdotcom/Tiket_Careers"
    )
    page_size = 20

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        detail_path = self._detail_path(job.url)
        response = self.client.get(f"{self.detail_api_base}{detail_path}")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise JobDetailStructureChangedError(
                f"{self.company}: Workday detail response is not JSON"
            ) from exc

        info = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
        if not isinstance(info, dict):
            raise JobDetailStructureChangedError(
                f"{self.company}: Workday jobPostingInfo is missing"
            )
        raw_id = info.get("jobReqId")
        description_html = info.get("jobDescription")
        if raw_id != job.id:
            raise JobDetailStructureChangedError(
                f"{self.company}: Workday detail returned the wrong job ID"
            )
        if not isinstance(description_html, str) or not description_html.strip():
            raise JobDetailStructureChangedError(
                f"{self.company}: Workday job description is missing"
            )
        description = self._clean_description_html(description_html)
        if not description:
            raise JobDetailStructureChangedError(
                f"{self.company}: Workday job description is empty"
            )

        return JobDetails(
            description_text=description,
            location=self._optional_text(info, "location"),
            employment_type=self._optional_text(info, "timeType"),
        )

    def _detail_path(self, job_url: str) -> str:
        path = urlparse(job_url).path
        prefix = "/Tiket_Careers"
        if not path.startswith(f"{prefix}/job/"):
            raise JobDetailStructureChangedError(
                f"{self.company}: unexpected Workday detail URL"
            )
        return path[len(prefix) :]

    def _optional_text(self, data: dict, key: str) -> str | None:
        value = data.get(key)
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise JobDetailStructureChangedError(
                f"{self.company}: Workday {key} field changed type"
            )
        return self._clean_inline(value) or None

    @staticmethod
    def _clean_description_html(value: str) -> str:
        soup = BeautifulSoup(value, "html.parser")
        for line_break in soup.find_all("br"):
            line_break.replace_with("\n")
        for item in soup.find_all("li"):
            item_text = TiketScraper._clean_inline(item.get_text(" ", strip=True))
            item.clear()
            item.append(f"- {item_text}" if item_text else "")
        return "\n".join(
            line
            for raw_line in soup.get_text("\n", strip=True).splitlines()
            if (line := TiketScraper._clean_inline(raw_line))
        )

    @staticmethod
    def _clean_inline(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    def extract_jobs(self, url: str) -> list[JobPosting]:
        offset = 0
        jobs: list[JobPosting] = []
        raw_ids: set[str] = set()
        advertised_total: int | None = None
        while True:
            body = {
                "appliedFacets": {},
                "limit": self.page_size,
                "offset": offset,
                "searchText": "engineer",
            }
            payload = self._json(self.client.post(self.api_url, json=body))
            jobs.extend(self.parse_api_payload(payload))
            records, total = payload.get("jobPostings"), payload.get("total")
            if not isinstance(records, list) or not isinstance(total, int):
                raise DOMStructureChangedError(f"{self.company}: pagination fields changed")
            if advertised_total is not None and total != advertised_total:
                raise DOMStructureChangedError(
                    f"{self.company}: result total changed during pagination"
                )
            advertised_total = total
            for record in records:
                bullets = record.get("bulletFields") if isinstance(record, dict) else None
                raw_id = bullets[0] if isinstance(bullets, list) and bullets else None
                if not isinstance(raw_id, str) or not raw_id:
                    raise DOMStructureChangedError(f"{self.company}: Workday job lacks an ID")
                raw_ids.add(raw_id)
            offset += len(records)
            if offset >= total:
                break
            if not records:
                raise DOMStructureChangedError(f"{self.company}: pagination returned no jobs")
        if len(raw_ids) != advertised_total:
            raise DOMStructureChangedError(
                f"{self.company}: loaded {len(raw_ids)} unique jobs but API advertised "
                f"{advertised_total}"
            )
        return jobs

    def parse_api_payload(self, payload: dict) -> list[JobPosting]:
        records, total = payload.get("jobPostings"), payload.get("total")
        if not isinstance(records, list) or not isinstance(total, int):
            raise DOMStructureChangedError(f"{self.company}: Workday fields changed")
        jobs: list[JobPosting] = []
        for record in records:
            if not isinstance(record, dict):
                raise DOMStructureChangedError(f"{self.company}: malformed Workday job")
            title, path, bullets = (
                record.get("title"),
                record.get("externalPath"),
                record.get("bulletFields"),
            )
            raw_id = bullets[0] if isinstance(bullets, list) and bullets else None
            if not all(isinstance(value, str) and value for value in (title, path, raw_id)):
                raise DOMStructureChangedError(f"{self.company}: Workday job lacks required fields")
            posted = record.get("postedOn")
            jobs.append(
                JobPosting(
                    id=raw_id,
                    title=title.strip(),
                    company=self.company,
                    url=absolute_url(f"{self.careers_base}/", path.lstrip("/")),
                    posted_date=posted if isinstance(posted, str) and posted else None,
                )
            )
        return matching_jobs(jobs)

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        root = soup.select_one('section[data-automation-id="jobResults"]')
        if root is None:
            raise DOMStructureChangedError(f"{self.company}: Workday results root is missing")
        cards = root.select(":scope > ul > li")
        found = root.select_one('[data-automation-id="jobFoundText"]')
        if not cards:
            if found and found.get_text(" ", strip=True).startswith("0 "):
                return []
            raise DOMStructureChangedError(f"{self.company}: Workday result cards are missing")

        jobs: list[JobPosting] = []
        for card in cards:
            title = required_text(card, 'a[data-automation-id="jobTitle"]', self.company)
            href = required_attr(
                card, 'a[data-automation-id="jobTitle"]', "href", self.company
            )
            subtitle = card.select_one('[data-automation-id="subtitle"] li')
            raw_id = subtitle.get_text(" ", strip=True) if subtitle else ""
            if not raw_id:
                raise DOMStructureChangedError(f"{self.company}: Workday job ID is missing")
            posted_date = None
            for term in card.find_all("dt"):
                if "posted on" in term.get_text(" ", strip=True).casefold():
                    value = term.find_next_sibling("dd")
                    posted_date = value.get_text(" ", strip=True) if value else None
                    break
            jobs.append(
                JobPosting(
                    id=raw_id,
                    title=title,
                    company=self.company,
                    url=absolute_url(f"{self.careers_base}/", href.lstrip("/")),
                    posted_date=posted_date,
                )
            )
        return matching_jobs(jobs)
