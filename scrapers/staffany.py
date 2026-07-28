import re

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import HTMLScraper
from scrapers.utils import absolute_url, id_from_url, matching_jobs


class StaffanyScraper(HTMLScraper):
    company = "StaffAny"

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        response = self.client.get(job.url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        role_sections: list[str] = []
        has_requirements = False
        has_responsibilities = False
        salary = None
        seen_sections: set[int] = set()
        for heading in soup.select("h1, h2, h3, h4, h5, h6"):
            label = _normalized_label(heading.get_text(" ", strip=True))
            section = heading.find_parent("section")
            if section is None:
                continue
            section_key = id(section)
            if label == "compensation":
                lines = _section_lines(section)
                salary = " ".join(lines[1:]).strip() or None
                continue
            section_kind = _section_kind(label)
            if section_kind is None or section_key in seen_sections:
                continue
            lines = _section_lines(section)
            if len(lines) < 2:
                continue
            seen_sections.add(section_key)
            role_sections.append("\n".join(lines))
            has_requirements |= section_kind == "requirements"
            has_responsibilities |= section_kind == "responsibilities"

        if not role_sections or not has_requirements or not has_responsibilities:
            raise JobDetailStructureChangedError(
                f"{self.company}: expected role and requirement sections are missing"
            )

        description = "\n\n".join(role_sections)
        searchable = f"{job.title}\n{description}".casefold()
        arrangement = None
        if re.search(r"\bremote(?: position| role| work)?\b", searchable):
            arrangement = "Remote"
        elif "hybrid" in searchable:
            arrangement = "Hybrid"
        elif re.search(r"\bon[ -]?site\b", searchable):
            arrangement = "On-site"

        employment_type = None
        if "contract" in searchable:
            employment_type = "Contract"
        elif re.search(r"\bfull[ -]?time\b", searchable):
            employment_type = "Full-time"
        elif re.search(r"\bpart[ -]?time\b", searchable):
            employment_type = "Part-time"

        return JobDetails(
            description_text=description,
            work_arrangement=arrangement,
            employment_type=employment_type,
            salary=salary,
        )

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        if soup.select_one("#eng") is None:
            raise DOMStructureChangedError(f"{self.company}: engineering section marker is missing")

        candidates = []
        hidden = {"elementor-hidden-desktop", "elementor-hidden-tablet", "elementor-hidden-mobile"}
        for section in soup.select("section.elementor-top-section"):
            classes = set(section.get("class") or [])
            link = section.select_one("a.elementor-button[href]")
            if link and not hidden.issubset(classes) and "career" in link.get("href", ""):
                candidates.append((section, link))
        if not candidates:
            raise DOMStructureChangedError(f"{self.company}: no visible career rows found")

        jobs: list[JobPosting] = []
        for section, link in candidates:
            paragraph = section.select_one(".elementor-widget-text-editor p")
            title = paragraph.get_text(" ", strip=True) if paragraph else ""
            href = link.get("href", "")
            if not title or not href:
                raise DOMStructureChangedError(f"{self.company}: malformed visible career row")
            job_url = absolute_url(source_url, href)
            jobs.append(
                JobPosting(
                    id=id_from_url(job_url, self.company),
                    title=title,
                    company=self.company,
                    url=job_url,
                )
            )
        return matching_jobs(jobs)


def _normalized_label(value: str) -> str:
    return " ".join(re.sub(r"[^a-z]+", " ", value.casefold()).split())


def _section_kind(label: str) -> str | None:
    if label in {"about the role", "role overview"}:
        return "overview"
    if (
        label.startswith("you are")
        or "requirement" in label
        or "qualification" in label
        or label in {"what you bring", "who you are"}
    ):
        return "requirements"
    if (
        label.startswith("you will")
        or "responsibilit" in label
        or label in {"what you will do", "what you ll do"}
    ):
        return "responsibilities"
    return None


def _section_lines(section) -> list[str]:
    return [
        " ".join(line.split())
        for line in section.get_text("\n", strip=True).splitlines()
        if line.strip()
    ]
