import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import BaseScraper
from scrapers.utils import absolute_url, matching_jobs, required_attr, required_text


class AmarthaScraper(BaseScraper):
    company = "Amartha"
    api_url = "https://apply.workable.com/api/v1/widget/accounts/amartha"
    detail_markdown_base = "https://apply.workable.com/amartha/jobs/view/"

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        response = self.client.get(
            f"{self.detail_markdown_base}{quote(job.id, safe='')}.md",
            headers={"Accept": "text/markdown"},
        )
        response.raise_for_status()
        markdown = response.text
        description = _markdown_section(markdown, "Description")
        requirements = _markdown_section(markdown, "Requirements")
        if not description or not requirements:
            raise JobDetailStructureChangedError(
                f"{self.company}: Workable description or requirements are missing"
            )

        location = None
        employment_type = None
        header = re.search(r"^>\s*(.+)$", markdown, re.MULTILINE)
        if header:
            parts = [part.strip() for part in header.group(1).split("·")]
            if len(parts) >= 3:
                location = parts[1] or None
                employment_type = parts[2] or None
        workplace = re.search(
            r"^\*\*Workplace:\*\*\s*(.+?)\s*$", markdown, re.MULTILINE
        )

        return JobDetails(
            description_text=(
                f"Description\n{description}\n\nRequirements\n{requirements}"
            ),
            location=location,
            work_arrangement=workplace.group(1).strip() if workplace else None,
            employment_type=employment_type,
        )

    def extract_jobs(self, url: str) -> list[JobPosting]:
        return self.parse_api_payload(self._json(self.client.get(self.api_url)))

    def parse_api_payload(self, payload: dict) -> list[JobPosting]:
        records = payload.get("jobs")
        if not isinstance(records, list):
            raise DOMStructureChangedError(f"{self.company}: jobs field is missing")
        jobs: list[JobPosting] = []
        for record in records:
            if not isinstance(record, dict):
                raise DOMStructureChangedError(f"{self.company}: malformed API job")
            raw_id, title, job_url = (
                record.get("shortcode"),
                record.get("title"),
                record.get("url"),
            )
            if not all(isinstance(value, str) and value for value in (raw_id, title, job_url)):
                raise DOMStructureChangedError(f"{self.company}: API job lacks required fields")
            date = record.get("published_on")
            jobs.append(
                JobPosting(
                    id=raw_id,
                    title=title.strip(),
                    company=self.company,
                    url=job_url,
                    posted_date=date if isinstance(date, str) and date else None,
                )
            )
        return matching_jobs(jobs)

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select('li[data-ui="job"]')
        if not cards:
            raise DOMStructureChangedError(f"{self.company}: no Workable job nodes found")
        jobs: list[JobPosting] = []
        for card in cards:
            raw_id = card.get("data-id")
            if not isinstance(raw_id, str) or not raw_id:
                raise DOMStructureChangedError(f"{self.company}: job node has no data-id")
            title = required_text(card, '[data-ui="job-title"]', self.company)
            href = required_attr(card, "a[href]", "href", self.company)
            posted = card.select_one('[data-ui="job-posted"]')
            jobs.append(
                JobPosting(
                    id=raw_id,
                    title=title,
                    company=self.company,
                    url=absolute_url("https://apply.workable.com", href),
                    posted_date=posted.get_text(" ", strip=True) if posted else None,
                )
            )
        return matching_jobs(jobs)


def _markdown_section(markdown: str, heading: str) -> str:
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        markdown,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if match is None:
        return ""
    body = match.group("body").strip()
    return re.sub(r"\n{3,}", "\n\n", body)
