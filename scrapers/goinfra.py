from abc import ABC, abstractmethod
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import BaseScraper
from scrapers.utils import matching_jobs


class GoInfraCareerScraper(BaseScraper, ABC):
    """Shared complete-result API used by GoTo and Gojek career pages."""

    api_url = "https://content.goinfra.co.id/ent-hris/career/job"
    company_code: str

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        parsed_url = urlsplit(job.url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise JobDetailStructureChangedError(
                f"{self.company}: job detail URL is invalid"
            )
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        response = self.client.get(
            self.api_url,
            params={
                "company": self.company_code,
                "search": "",
                "location": "",
                "department": "",
            },
            headers={"Origin": origin},
        )
        try:
            payload = self._json(response)
        except DOMStructureChangedError as exc:
            raise JobDetailStructureChangedError(
                f"{self.company}: expected a JSON job-detail response"
            ) from exc
        return self.parse_detail_payload(payload, job.id)

    def parse_detail_payload(self, payload: dict, job_id: str) -> JobDetails:
        data = payload.get("data")
        if str(payload.get("code")) != "200000" or not isinstance(data, dict):
            raise JobDetailStructureChangedError(
                f"{self.company}: invalid job-detail API envelope"
            )
        groups, page, total = data.get("items"), data.get("page"), data.get("total")
        if (
            not isinstance(groups, list)
            or page != 1
            or not isinstance(total, int)
            or total < 0
        ):
            raise JobDetailStructureChangedError(
                f"{self.company}: job-detail API metadata changed"
            )

        matches: list[dict] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("job_list"), list):
                raise JobDetailStructureChangedError(
                    f"{self.company}: malformed job-detail department group"
                )
            for record in group["job_list"]:
                if not isinstance(record, dict):
                    raise JobDetailStructureChangedError(
                        f"{self.company}: malformed job-detail API record"
                    )
                raw_id = record.get("id")
                if not isinstance(raw_id, str) or not raw_id:
                    raise JobDetailStructureChangedError(
                        f"{self.company}: job-detail API record lacks an ID"
                    )
                if raw_id == job_id:
                    matches.append(record)

        if not matches:
            raise JobDetailStructureChangedError(
                f"{self.company}: requested job is no longer present in the public API"
            )
        if len(matches) != 1:
            raise JobDetailStructureChangedError(
                f"{self.company}: requested job appears more than once in the public API"
            )
        record = matches[0]
        channels = record.get("distributionChannels")
        if record.get("state") != "published" or not (
            isinstance(channels, list) and "public" in channels
        ):
            raise JobDetailStructureChangedError(
                f"{self.company}: requested job is no longer publicly available"
            )

        content = record.get("content")
        if not isinstance(content, dict):
            raise JobDetailStructureChangedError(
                f"{self.company}: embedded job-detail content is missing"
            )
        description_html, content_lists = (
            content.get("descriptionHtml"),
            content.get("lists"),
        )
        if not isinstance(description_html, str) or not isinstance(content_lists, list):
            raise JobDetailStructureChangedError(
                f"{self.company}: embedded job-detail fields changed"
            )

        sections: list[str] = []
        description = self._html_text(description_html)
        if description:
            sections.append(description)
        for section in content_lists:
            if not isinstance(section, dict):
                raise JobDetailStructureChangedError(
                    f"{self.company}: malformed embedded job-detail section"
                )
            heading, section_html = section.get("text"), section.get("content")
            if (
                not isinstance(heading, str)
                or not heading.strip()
                or not isinstance(section_html, str)
            ):
                raise JobDetailStructureChangedError(
                    f"{self.company}: malformed embedded job-detail section"
                )
            section_text = self._html_text(section_html)
            if not section_text:
                raise JobDetailStructureChangedError(
                    f"{self.company}: embedded job-detail section is empty"
                )
            sections.extend((" ".join(heading.split()), section_text))
        if not sections:
            raise JobDetailStructureChangedError(
                f"{self.company}: embedded job description is empty"
            )

        categories = record.get("categories")
        if categories is not None and not isinstance(categories, dict):
            raise JobDetailStructureChangedError(
                f"{self.company}: job-detail categories changed"
            )
        detail = record.get("job_detail")
        if detail is not None and not isinstance(detail, dict):
            raise JobDetailStructureChangedError(
                f"{self.company}: nested job-detail metadata changed"
            )
        categories = categories or {}
        detail = detail or {}

        return JobDetails(
            description_text="\n".join(sections),
            location=self._first_optional_text(
                record.get("full_location"),
                detail.get("full_location"),
                categories.get("location"),
            ),
            work_arrangement=self._first_optional_text(record.get("workplaceType")),
            employment_type=self._first_optional_text(
                detail.get("employee_type"), categories.get("commitment")
            ),
            salary=self._first_optional_text(record.get("salary")),
        )

    @staticmethod
    def _html_text(value: str) -> str:
        soup = BeautifulSoup(value, "html.parser")
        return "\n".join(
            " ".join(part.split()) for part in soup.stripped_strings if part.strip()
        )

    def _first_optional_text(self, *values: object) -> str | None:
        for value in values:
            if value is None:
                continue
            if not isinstance(value, str):
                raise JobDetailStructureChangedError(
                    f"{self.company}: embedded job-detail metadata changed"
                )
            if value.strip():
                return value.strip()
        return None

    def extract_jobs(self, url: str) -> list[JobPosting]:
        parsed_url = urlsplit(url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        response = self.client.get(
            self.api_url,
            params={
                "company": self.company_code,
                "search": "",
                "location": "",
                "department": "",
            },
            headers={"Origin": origin},
        )
        return self.parse_api_payload(self._json(response))

    def parse_api_payload(self, payload: dict) -> list[JobPosting]:
        data = payload.get("data")
        if str(payload.get("code")) != "200000" or not isinstance(data, dict):
            raise DOMStructureChangedError(f"{self.company}: invalid API response envelope")

        groups, page, total = data.get("items"), data.get("page"), data.get("total")
        if (
            not isinstance(groups, list)
            or page != 1
            or not isinstance(total, int)
            or total < 0
        ):
            raise DOMStructureChangedError(
                f"{self.company}: complete-result metadata changed"
            )

        raw_ids: set[str] = set()
        jobs: list[JobPosting] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("job_list"), list):
                raise DOMStructureChangedError(
                    f"{self.company}: malformed API department group"
                )
            for record in group["job_list"]:
                if not isinstance(record, dict):
                    raise DOMStructureChangedError(f"{self.company}: malformed API job")
                raw_id, title = record.get("id"), record.get("text")
                if (
                    not isinstance(raw_id, str)
                    or not raw_id
                    or not isinstance(title, str)
                    or not title.strip()
                ):
                    raise DOMStructureChangedError(
                        f"{self.company}: API job lacks ID or title"
                    )
                raw_ids.add(raw_id)
                channels = record.get("distributionChannels")
                if record.get("state") != "published" or not (
                    isinstance(channels, list) and "public" in channels
                ):
                    continue
                jobs.append(
                    JobPosting(
                        id=raw_id,
                        title=title.strip(),
                        company=self.company,
                        url=self.job_url(raw_id, title.strip()),
                    )
                )

        if len(raw_ids) != total:
            raise DOMStructureChangedError(
                f"{self.company}: loaded {len(raw_ids)} unique jobs but API advertised "
                f"{total}"
            )
        return matching_jobs(jobs)

    @abstractmethod
    def job_url(self, raw_id: str, title: str) -> str:
        """Build the public company-specific career detail URL."""
