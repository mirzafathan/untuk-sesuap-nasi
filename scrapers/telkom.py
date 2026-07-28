import re
from urllib.parse import quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from exceptions import DOMStructureChangedError, JobDetailStructureChangedError
from models import JobDetails, JobPosting
from scrapers.base import BaseScraper
from scrapers.utils import id_from_url, matching_jobs


class TelkomScraper(BaseScraper):
    company = "Telkom"
    api_url = "https://apicareers.telkom.co.id/v1/frontend/en/job/search"
    detail_api_url = (
        "https://apicareers.telkom.co.id/v1/frontend/en/job/detail-slug/{slug}"
    )
    careers_url = "https://careers.telkom.co.id/search-jobs"

    def extract_job_details(self, job: JobPosting) -> JobDetails:
        parsed = urlparse(job.url)
        route_prefix = "/detail-job/"
        if (
            parsed.hostname != "careers.telkom.co.id"
            or not parsed.path.startswith(route_prefix)
        ):
            raise JobDetailStructureChangedError(
                f"{self.company}: job detail URL no longer matches the expected route"
            )
        slug = unquote(parsed.path[len(route_prefix) :]).strip("/")
        if not slug or "/" in slug:
            raise JobDetailStructureChangedError(
                f"{self.company}: job detail URL lacks a valid slug"
            )

        try:
            api_auth = self._discover_public_api_auth(self.careers_url)
        except DOMStructureChangedError as exc:
            raise JobDetailStructureChangedError(
                f"{self.company}: public job-detail API authentication changed"
            ) from exc
        response = self.client.get(
            self.detail_api_url.format(slug=quote(slug, safe="-")),
            auth=api_auth,
            headers={"Origin": "https://careers.telkom.co.id"},
        )
        try:
            payload = self._json(response)
        except DOMStructureChangedError as exc:
            raise JobDetailStructureChangedError(
                f"{self.company}: expected a JSON job-detail response"
            ) from exc
        return self.parse_detail_payload(payload)

    def parse_detail_payload(self, payload: dict) -> JobDetails:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise JobDetailStructureChangedError(
                f"{self.company}: job-detail data field is missing"
            )

        responsibilities = self._detail_items(
            data.get("job_desc"), "desc_sequence", "desc"
        )
        requirements = self._detail_items(
            data.get("job_requirement"),
            "requirement_sequence",
            "requirement",
        )
        long_description = data.get("long_desc")
        if long_description is not None and not isinstance(long_description, str):
            raise JobDetailStructureChangedError(
                f"{self.company}: main job description changed"
            )

        sections: list[str] = []
        if isinstance(long_description, str):
            cleaned_description = self._clean_detail_text(long_description)
            if cleaned_description:
                sections.append(cleaned_description)
        if responsibilities:
            sections.append(
                "Responsibilities\n"
                + "\n".join(f"- {item}" for item in responsibilities)
            )
        if requirements:
            sections.append(
                "Requirements\n" + "\n".join(f"- {item}" for item in requirements)
            )
        if not sections:
            raise JobDetailStructureChangedError(
                f"{self.company}: job-detail response contains no description"
            )

        return JobDetails(
            description_text="\n".join(sections),
            location=self._nested_detail_name(data.get("location"), "location"),
            employment_type=self._nested_detail_name(
                data.get("job_type"), "employment type"
            ),
        )

    def _detail_items(
        self, value: object, sequence_key: str, text_key: str
    ) -> list[str]:
        if not isinstance(value, list):
            raise JobDetailStructureChangedError(
                f"{self.company}: job-detail list fields changed"
            )
        ordered: list[tuple[int, str]] = []
        for item in value:
            if not isinstance(item, dict):
                raise JobDetailStructureChangedError(
                    f"{self.company}: malformed job-detail list item"
                )
            sequence, text = item.get(sequence_key), item.get(text_key)
            if (
                not isinstance(sequence, int)
                or not isinstance(text, str)
                or not self._clean_detail_text(text)
            ):
                raise JobDetailStructureChangedError(
                    f"{self.company}: malformed job-detail list item"
                )
            ordered.append((sequence, self._clean_detail_text(text)))
        ordered.sort(key=lambda item: item[0])
        return [text for _, text in ordered]

    def _nested_detail_name(self, value: object, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise JobDetailStructureChangedError(
                f"{self.company}: job-detail {field} changed"
            )
        name = value.get("name")
        if name is None:
            return None
        if not isinstance(name, str):
            raise JobDetailStructureChangedError(
                f"{self.company}: job-detail {field} changed"
            )
        return name.strip() or None

    @staticmethod
    def _clean_detail_text(value: str) -> str:
        soup = BeautifulSoup(value, "html.parser")
        return " ".join(soup.get_text(" ", strip=True).split())

    def extract_jobs(self, url: str) -> list[JobPosting]:
        api_auth = self._discover_public_api_auth(url)
        page = 1
        jobs: list[JobPosting] = []
        raw_slugs: set[str] = set()
        expected_last_page: int | None = None
        advertised_total: int | None = None
        while True:
            body = {
                "FilterText": "engineer",
                "FilterDepartmentId": None,
                "FilterLocationId": None,
                "FilterJobFunctionalId": None,
                "FilterJobTypeId": None,
                "FilterJobRoleId": None,
            }
            response = self.client.post(
                self.api_url,
                params={"page": page} if page > 1 else None,
                json=body,
                auth=api_auth,
                headers={"Origin": "https://careers.telkom.co.id"},
            )
            payload = self._json(response)
            jobs.extend(self.parse_api_payload(payload))
            data = payload.get("data")
            last_page = data.get("last_page") if isinstance(data, dict) else None
            records = data.get("data") if isinstance(data, dict) else None
            total = data.get("total") if isinstance(data, dict) else None
            if (
                not isinstance(last_page, int)
                or not isinstance(records, list)
                or not isinstance(total, int)
            ):
                raise DOMStructureChangedError(f"{self.company}: pagination fields changed")
            if expected_last_page is not None and last_page != expected_last_page:
                raise DOMStructureChangedError(
                    f"{self.company}: last_page changed during pagination"
                )
            if advertised_total is not None and total != advertised_total:
                raise DOMStructureChangedError(
                    f"{self.company}: result total changed during pagination"
                )
            expected_last_page, advertised_total = last_page, total
            for record in records:
                slug = record.get("short_desc_url") if isinstance(record, dict) else None
                if not isinstance(slug, str) or not slug:
                    raise DOMStructureChangedError(f"{self.company}: API job lacks a URL slug")
                raw_slugs.add(slug)
            if page >= last_page:
                break
            if not records:
                raise DOMStructureChangedError(
                    f"{self.company}: pagination returned no jobs before the last page"
                )
            page += 1
        if len(raw_slugs) != advertised_total:
            raise DOMStructureChangedError(
                f"{self.company}: loaded {len(raw_slugs)} unique jobs but API advertised "
                f"{advertised_total}"
            )
        return jobs

    def _discover_public_api_auth(self, careers_url: str) -> tuple[str, str]:
        page_response = self.client.get(careers_url)
        page_response.raise_for_status()
        soup = BeautifulSoup(page_response.text, "html.parser")
        main_script = next(
            (
                script.get("src")
                for script in soup.find_all("script", src=True)
                if urlparse(script.get("src", "")).path.rsplit("/", 1)[-1].startswith("main~")
            ),
            None,
        )
        if not isinstance(main_script, str):
            raise DOMStructureChangedError(f"{self.company}: main browser bundle is missing")
        main_url = urljoin(careers_url, main_script)
        main_response = self.client.get(main_url)
        main_response.raise_for_status()
        chunk_match = re.search(r'(?:^|[,{}])8:"([a-f0-9]{20})"', main_response.text)
        if chunk_match is None:
            raise DOMStructureChangedError(f"{self.company}: public API auth bundle changed")
        version = urlparse(main_url).query
        chunk_path = f"/8.{chunk_match.group(1)}.js"
        if version:
            chunk_path = f"{chunk_path}?{version}"
        chunk_response = self.client.get(urljoin(careers_url, chunk_path))
        chunk_response.raise_for_status()
        credentials = re.search(
            r'username:"([^"]+)",password:"([^"]+)"', chunk_response.text
        )
        if credentials is None:
            raise DOMStructureChangedError(f"{self.company}: public API auth fields changed")
        return credentials.group(1), credentials.group(2)

    def parse_api_payload(self, payload: dict) -> list[JobPosting]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DOMStructureChangedError(f"{self.company}: API data field is missing")
        records, total = data.get("data"), data.get("total")
        if not isinstance(records, list) or not isinstance(total, int):
            raise DOMStructureChangedError(f"{self.company}: API job fields changed")
        jobs: list[JobPosting] = []
        for record in records:
            if not isinstance(record, dict):
                raise DOMStructureChangedError(f"{self.company}: malformed API job")
            title, slug = record.get("name"), record.get("short_desc_url")
            if not isinstance(title, str) or not title or not isinstance(slug, str) or not slug:
                raise DOMStructureChangedError(f"{self.company}: API job lacks title or URL slug")
            job_url = f"https://careers.telkom.co.id/detail-job/{slug}"
            jobs.append(
                JobPosting(
                    id=slug.rsplit("-", 1)[-1] or id_from_url(job_url, self.company),
                    title=title.strip(),
                    company=self.company,
                    url=job_url,
                )
            )
        return matching_jobs(jobs)

    def parse_html(self, html: str, source_url: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        if "Belum ada lowongan tersedia." in text or "Stay tuned for future openings" in text:
            return []
        links = soup.select('a[href*="/detail-job/"]')
        if not links:
            raise DOMStructureChangedError(f"{self.company}: no job cards or empty-state marker")
        jobs: list[JobPosting] = []
        for link in links:
            title = link.get_text(" ", strip=True)
            href = link.get("href", "")
            if not title or not href:
                raise DOMStructureChangedError(f"{self.company}: malformed job card")
            job_url = f"https://careers.telkom.co.id{href}" if href.startswith("/") else href
            jobs.append(
                JobPosting(
                    id=id_from_url(job_url, self.company).rsplit("-", 1)[-1],
                    title=title,
                    company=self.company,
                    url=job_url,
                )
            )
        return matching_jobs(jobs)
