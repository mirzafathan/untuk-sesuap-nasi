import re
import json
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import HTMLScraper
from scrapers.utils import id_from_url, matching_jobs, required_text


class MekariScraper(HTMLScraper):
    company = "Mekari"

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        response = self.client.get(job.url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        payload: dict | None = None
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                candidate = json.loads(script.string or script.get_text(), strict=False)
            except (TypeError, ValueError):
                continue
            if isinstance(candidate, dict) and _is_job_posting(candidate.get("@type")):
                payload = candidate
                break
        if payload is None:
            raise JobDetailStructureChangedError(
                f"{self.company}: JobPosting JSON-LD is missing or malformed"
            )

        raw_description = payload.get("description")
        description = _html_to_text(unescape(raw_description)) if isinstance(
            raw_description, str
        ) else ""
        if not description:
            raise JobDetailStructureChangedError(
                f"{self.company}: structured job description is missing"
            )

        employment_type = payload.get("employmentType")
        if not isinstance(employment_type, str) or not employment_type.strip():
            employment_type = None

        arrangement = None
        for node in soup.select(".opening-info span"):
            value = node.get_text(" ", strip=True).lstrip("| ").strip()
            if re.search(r"\b(?:partially remote|remote|hybrid|on[ -]?site)\b", value, re.I):
                arrangement = value
                break

        return JobDetails(
            description_text=description,
            location=_job_location(payload.get("jobLocation")),
            work_arrangement=arrangement,
            employment_type=employment_type,
        )

    def extract_jobs(self, url: str) -> list[JobPosting]:
        current_url: str | None = url
        jobs: list[JobPosting] = []
        visited: set[str] = set()
        raw_job_urls: set[str] = set()
        advertised_total: int | None = None
        while current_url:
            if current_url in visited:
                raise DOMStructureChangedError(
                    f"{self.company}: pagination repeated a page before completion"
                )
            visited.add(current_url)
            response = self.client.get(current_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            cards = soup.select(".js-careers-page-job-list-item")
            if not cards:
                raise DOMStructureChangedError(f"{self.company}: no job-list items found")
            for card in cards:
                link = card.select_one("a[href]")
                href = link.get("href") if link else card.get("data-href")
                if not isinstance(href, str) or not href:
                    raise DOMStructureChangedError(
                        f"{self.company}: job card is missing its URL"
                    )
                raw_job_urls.add(urljoin(str(response.url), href))

            total_node = soup.select_one(".pagination-info")
            total_match = re.search(
                r"\bof\s+([\d,]+)\s+Jobs\b",
                total_node.get_text(" ", strip=True) if total_node else "",
                re.IGNORECASE,
            )
            if total_match is None:
                total_match = re.search(
                    r"\btotal_results\s*:\s*['\"]?([\d,]+)", response.text
                )
            if total_match is None:
                raise DOMStructureChangedError(
                    f"{self.company}: advertised job total is missing"
                )
            page_total = int(total_match.group(1).replace(",", ""))
            if advertised_total is not None and page_total != advertised_total:
                raise DOMStructureChangedError(
                    f"{self.company}: advertised job total changed during pagination"
                )
            advertised_total = page_total
            jobs.extend(self.parse_html(response.text, str(response.url)))
            next_link = next(
                (
                    link
                    for link in soup.select("ul.pagination a.page-link[href]")
                    if link.get_text(" ", strip=True).casefold() == "next"
                    and "disabled" not in (link.parent.get("class") or [])
                ),
                None,
            )
            if next_link:
                next_href: str | None = next_link["href"]
            else:
                metadata_next = re.search(
                    r"\bnext_page_url\s*:\s*(?:null|'([^']*)'|\"([^\"]*)\")",
                    response.text,
                )
                next_href = (
                    unescape(metadata_next.group(1) or metadata_next.group(2))
                    if metadata_next and (metadata_next.group(1) or metadata_next.group(2))
                    else None
                )
            current_url = urljoin(str(response.url), next_href) if next_href else None
        if advertised_total is None or len(raw_job_urls) != advertised_total:
            raise DOMStructureChangedError(
                f"{self.company}: loaded {len(raw_job_urls)} jobs but advertised "
                f"{advertised_total}"
            )
        return jobs

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".js-careers-page-job-list-item")
        if not cards:
            raise DOMStructureChangedError(f"{self.company}: no job-list items found")

        jobs: list[JobPosting] = []
        for card in cards:
            title = required_text(card, ".js-job-list-opening-name", self.company)
            link = card.select_one("a[href]")
            href = link.get("href") if link else card.get("data-href")
            if not isinstance(href, str) or not href:
                raise DOMStructureChangedError(f"{self.company}: job card is missing its URL")
            job_url = urljoin(source_url, href)
            jobs.append(
                JobPosting(
                    id=id_from_url(job_url, self.company),
                    title=title,
                    company=self.company,
                    url=job_url,
                )
            )
        return matching_jobs(jobs)


def _is_job_posting(value: object) -> bool:
    if isinstance(value, str):
        return value.casefold() == "jobposting"
    if isinstance(value, list):
        return any(_is_job_posting(item) for item in value)
    return False


def _html_to_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for node in soup.select("script, style"):
        node.decompose()
    return "\n".join(
        " ".join(line.split())
        for line in soup.get_text("\n", strip=True).splitlines()
        if line.strip()
    )


def _job_location(value: object) -> str | None:
    locations = value if isinstance(value, list) else [value]
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if not isinstance(address, dict):
            continue
        parts: list[str] = []
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            part = address.get(key)
            if isinstance(part, dict):
                part = part.get("name")
            if (
                isinstance(part, str)
                and part.strip()
                and all(part.strip().casefold() != existing.casefold() for existing in parts)
            ):
                parts.append(part.strip())
        if parts:
            return ", ".join(parts)
    return None
