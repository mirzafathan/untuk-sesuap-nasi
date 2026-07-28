import inspect

import pytest

from main import TARGETS
from scrapers.base import BaseScraper


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.name)
def test_every_registered_scraper_implements_job_detail_extraction(target) -> None:
    scraper = target.factory()
    try:
        implementation = inspect.getattr_static(type(scraper), "extract_job_details")

        assert implementation is not BaseScraper.extract_job_details
    finally:
        scraper.close()
