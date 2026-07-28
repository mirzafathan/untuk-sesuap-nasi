from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import BaseScraper
from scrapers.utils import absolute_url, id_from_url, matching_jobs, required_text


class ShopeeScraper(BaseScraper):
    company = "Shopee"
    api_url = "https://ats.workatsea.com/ats/api/v1/user/job/list/"
    detail_base = "https://careers.shopee.co.id/job-detail/"
    page_size = 50
    detail_api_url = "https://careers.shopee.co.id/api/positions/detail/"

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        response = self.client.get(
            self.detail_api_url,
            params={"id": job.id},
            headers={"Accept": "application/json"},
        )
        try:
            payload = self._json(response)
        except DOMStructureChangedError as exc:
            raise JobDetailStructureChangedError(
                f"{self.company}: expected a JSON job-detail response"
            ) from exc

        if str(payload.get("id", "")) != job.id:
            raise JobDetailStructureChangedError(
                f"{self.company}: detail response has a missing or mismatched job ID"
            )
        presentations = payload.get("position_presentation")
        if not isinstance(presentations, list) or not presentations:
            raise JobDetailStructureChangedError(
                f"{self.company}: position_presentation is missing"
            )

        valid_presentations = [item for item in presentations if isinstance(item, dict)]
        presentation = next(
            (
                item
                for item in valid_presentations
                if isinstance(item.get("lang_code"), str)
                and item["lang_code"].casefold().startswith("en")
            ),
            valid_presentations[0] if valid_presentations else None,
        )
        if presentation is None:
            raise JobDetailStructureChangedError(
                f"{self.company}: position presentation is malformed"
            )

        responsibilities = _html_to_text(presentation.get("job_description"))
        requirements = _html_to_text(presentation.get("job_requirement"))
        if not responsibilities or not requirements:
            raise JobDetailStructureChangedError(
                f"{self.company}: job description or requirements are missing"
            )
        team_context = _html_to_text(presentation.get("sub_team_description"))
        sections = []
        if team_context:
            sections.append(f"Team\n{team_context}")
        sections.extend(
            [
                f"Responsibilities\n{responsibilities}",
                f"Requirements\n{requirements}",
            ]
        )
        return JobDetails(description_text="\n\n".join(sections))

    def extract_jobs(self, url: str) -> list[JobPosting]:
        query = parse_qs(urlparse(url).query)
        search = query.get("name", [""])[0]
        offset = 0
        all_jobs: list[JobPosting] = []
        raw_ids: set[str] = set()
        advertised_total: int | None = None
        while True:
            response = self.client.get(
                self.api_url,
                params={"limit": self.page_size, "offset": offset, "search_content": search},
                headers={"Origin": "https://careers.shopee.co.id"},
            )
            payload = self._json(response)
            data = payload.get("data")
            if payload.get("code") != 0 or not isinstance(data, dict):
                raise DOMStructureChangedError(f"{self.company}: invalid API response envelope")
            raw_jobs = data.get("job_list")
            if not isinstance(raw_jobs, list):
                raise DOMStructureChangedError(f"{self.company}: missing job_list")
            all_jobs.extend(self.parse_api_payload(data))
            total = data.get("total_count")
            if not isinstance(total, int):
                raise DOMStructureChangedError(f"{self.company}: missing total_count")
            if advertised_total is not None and total != advertised_total:
                raise DOMStructureChangedError(
                    f"{self.company}: total_count changed during pagination"
                )
            advertised_total = total
            for record in raw_jobs:
                raw_id = record.get("id") if isinstance(record, dict) else None
                if raw_id in (None, ""):
                    raise DOMStructureChangedError(f"{self.company}: API job lacks an ID")
                raw_ids.add(str(raw_id))
            offset += len(raw_jobs)
            if offset >= total:
                break
            if not raw_jobs:
                raise DOMStructureChangedError(f"{self.company}: pagination returned no jobs")
        if len(raw_ids) != advertised_total:
            raise DOMStructureChangedError(
                f"{self.company}: loaded {len(raw_ids)} unique jobs but API advertised "
                f"{advertised_total}"
            )
        return all_jobs

    def parse_api_payload(self, payload: dict) -> list[JobPosting]:
        records = payload.get("job_list")
        if not isinstance(records, list) or not isinstance(payload.get("total_count"), int):
            raise DOMStructureChangedError(f"{self.company}: job API fields changed")
        jobs: list[JobPosting] = []
        for record in records:
            if not isinstance(record, dict):
                raise DOMStructureChangedError(f"{self.company}: malformed API job")
            raw_id, title = record.get("id"), record.get("job_name")
            if raw_id in (None, "") or not isinstance(title, str) or not title.strip():
                raise DOMStructureChangedError(f"{self.company}: API job lacks ID or title")
            jobs.append(
                JobPosting(
                    id=str(raw_id),
                    title=title.strip(),
                    company=self.company,
                    url=f"{self.detail_base}{raw_id}",
                )
            )
        return matching_jobs(jobs)

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("ul.position-list > li.item")
        if not cards:
            raise DOMStructureChangedError(f"{self.company}: no position cards found")
        jobs: list[JobPosting] = []
        for card in cards:
            title = required_text(card, ".title", self.company)
            link = card.select_one("a.item-link[href]")
            if link is None:
                raise DOMStructureChangedError(f"{self.company}: position card has no detail URL")
            job_url = absolute_url(source_url, link["href"])
            jobs.append(
                JobPosting(
                    id=id_from_url(job_url, self.company),
                    title=title,
                    company=self.company,
                    url=job_url,
                )
            )
        return matching_jobs(jobs)


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
