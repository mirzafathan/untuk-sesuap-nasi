from urllib.parse import urljoin

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError
from models import JobPosting
from scrapers.goinfra import GoInfraCareerScraper
from scrapers.utils import id_from_url, matching_jobs, required_text


class GoToScraper(GoInfraCareerScraper):
    company = "GoTo"
    company_code = "HoldCo"
    base_url = "https://www.gotocompany.com"

    def job_url(self, raw_id: str, title: str) -> str:
        return f"{self.base_url}/en/careers/{raw_id}"

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select('a[href^="/en/careers/"]')
        if not cards:
            raise DOMStructureChangedError(f"{self.company}: no career cards found")

        jobs: list[JobPosting] = []
        for card in cards:
            title = required_text(card, '[class*="cssTitle__"]', self.company)
            job_url = urljoin(self.base_url, card.get("href", ""))
            jobs.append(
                JobPosting(
                    id=id_from_url(job_url, self.company),
                    title=title,
                    company=self.company,
                    url=job_url,
                )
            )
        return matching_jobs(jobs)
