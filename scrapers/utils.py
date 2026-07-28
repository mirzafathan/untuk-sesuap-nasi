import re
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import Tag

from exceptions import DOMStructureChangedError
from job_filter import matches_target_role
from models import JobPosting


def required_text(node: Tag, selector: str, company: str) -> str:
    selected = node.select_one(selector)
    value = selected.get_text(" ", strip=True) if selected else ""
    if not value:
        raise DOMStructureChangedError(f"{company}: missing text for selector {selector!r}")
    return value


def required_attr(node: Tag, selector: str, attribute: str, company: str) -> str:
    selected = node.select_one(selector)
    value = selected.get(attribute) if selected else None
    if not isinstance(value, str) or not value.strip():
        raise DOMStructureChangedError(
            f"{company}: missing {attribute!r} for selector {selector!r}"
        )
    return value.strip()


def id_from_url(url: str, company: str) -> str:
    path = urlparse(url).path.rstrip("/")
    value = path.rsplit("/", 1)[-1] if path else ""
    if not value:
        raise DOMStructureChangedError(f"{company}: unable to derive a job ID from {url!r}")
    return value


def absolute_url(base: str, value: str) -> str:
    return urljoin(base, value)


def matching_jobs(jobs: Iterable[JobPosting]) -> list[JobPosting]:
    return [job for job in jobs if matches_target_role(job.title)]


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")

