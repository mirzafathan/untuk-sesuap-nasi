from urllib.parse import urljoin

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError
from models import JobPosting
from scrapers.goinfra import GoInfraCareerScraper
from scrapers.utils import id_from_url, matching_jobs, required_text, slugify


class GojekScraper(GoInfraCareerScraper):
    company = "Gojek"
    company_code = "ODS"
    base_url = "https://www.gojek.io"

    def job_url(self, raw_id: str, title: str) -> str:
        return f"{self.base_url}/careers/view/{slugify(title)}/{raw_id}"

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select('a.table-row[href*="/careers/view/"]')
        if not cards:
            raise DOMStructureChangedError(f"{self.company}: no career rows found")

        jobs: list[JobPosting] = []
        for card in cards:
            title = required_text(card, ".col-md-6 .mb-0", self.company)
            href = card.get("href")
            if not isinstance(href, str) or not href:
                raise DOMStructureChangedError(f"{self.company}: career row lacks a URL")
            job_url = urljoin(self.base_url, href)
            jobs.append(
                JobPosting(
                    id=id_from_url(job_url, self.company),
                    title=title,
                    company=self.company,
                    url=job_url,
                )
            )
        return matching_jobs(jobs)
