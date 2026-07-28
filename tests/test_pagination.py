import json

import httpx
import pytest

from exceptions import DOMStructureChangedError
from scrapers.blibli import BlibliScraper
from scrapers.bytedance import ByteDanceScraper
from scrapers.mekari import MekariScraper
from scrapers.gojek import GojekScraper
from scrapers.goto import GoToScraper
from scrapers.grab import GrabScraper
from scrapers.shopee import ShopeeScraper
from scrapers.stockbit import StockbitScraper
from scrapers.telkom import TelkomScraper
from scrapers.tiket import TiketScraper
from scrapers.traveloka import TravelokaScraper


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_shopee_fetches_every_page_until_total_count() -> None:
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        records = (
            [
                {"id": 1, "job_name": "Backend Engineer"},
                {"id": 2, "job_name": "Software Engineer"},
            ]
            if offset == 0
            else [{"id": 3, "job_name": "AI Engineer"}]
        )
        return httpx.Response(
            200,
            json={"code": 0, "data": {"job_list": records, "total_count": 3}},
        )

    jobs = ShopeeScraper(client=client(handler)).extract_jobs(
        "https://careers.shopee.co.id/jobs?name=engineer"
    )

    assert offsets == [0, 2]
    assert [job.id for job in jobs] == ["1", "2", "3"]


def test_shopee_rejects_repeated_records_that_only_appear_to_reach_total() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "job_list": [
                        {"id": 1, "job_name": "Backend Engineer"},
                        {"id": 1, "job_name": "Backend Engineer"},
                    ],
                    "total_count": 2,
                },
            },
        )

    with pytest.raises(DOMStructureChangedError, match="1 unique jobs"):
        ShopeeScraper(client=client(handler)).extract_jobs(
            "https://careers.shopee.co.id/jobs?name=engineer"
        )


def mekari_page(ids: list[int], total: int, next_href: str | None) -> str:
    cards = "".join(
        f'<div class="js-careers-page-job-list-item" data-href="/jobs/{raw_id}">'
        f'<span class="js-job-list-opening-name">Backend Engineer {raw_id}</span></div>'
        for raw_id in ids
    )
    next_link = (
        f'<li class="page-item"><a class="page-link" href="{next_href}">Next</a></li>'
        if next_href
        else ""
    )
    return (
        f"{cards}<span class=\"pagination-info\">1 - 2 of {total} Jobs</span>"
        f'<ul class="pagination">{next_link}</ul>'
    )


def test_mekari_follows_next_links_until_advertised_total() -> None:
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("p", "1")
        pages.append(page)
        html = (
            mekari_page([1, 2], 3, "/?q=engineer&limit=25&p=2")
            if page == "1"
            else mekari_page([3], 3, None)
        )
        return httpx.Response(200, text=html)

    jobs = MekariScraper(client=client(handler)).extract_jobs(
        "https://mekari.example/?q=engineer&limit=25"
    )

    assert pages == ["1", "2"]
    assert [job.id for job in jobs] == ["1", "2", "3"]


def test_mekari_does_not_silently_finish_before_advertised_total() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=mekari_page([1], 3, None))
    )

    with pytest.raises(DOMStructureChangedError, match="advertised 3"):
        MekariScraper(client=httpx.Client(transport=transport)).extract_jobs(
            "https://mekari.example/?q=engineer"
        )


def test_mekari_uses_server_metadata_before_react_renders_pagination() -> None:
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("p", "1")
        pages.append(page)
        cards = mekari_page([1], 1, None).split('<span class="pagination-info">')[0]
        if page == "1":
            metadata = (
                "<script>var RB={init_data:{paginator:{"
                "next_page_url:'?q=engineer&amp;p=2',total_results:'2'}}}</script>"
            )
        else:
            cards = cards.replace("/jobs/1", "/jobs/2").replace(
                "Engineer 1", "Engineer 2"
            )
            metadata = (
                "<script>var RB={init_data:{paginator:{"
                "next_page_url:null,total_results:'2'}}}</script>"
            )
        return httpx.Response(200, text=cards + metadata)

    jobs = MekariScraper(client=client(handler)).extract_jobs(
        "https://mekari.example/?q=engineer"
    )

    assert pages == ["1", "2"]
    assert [job.id for job in jobs] == ["1", "2"]


def test_blibli_follows_pagination_tokens_until_none() -> None:
    tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("paginationToken")
        tokens.append(token)
        suffix = "1" if token is None else "2"
        return httpx.Response(
            200,
            json={
                "paginationToken": "page-2" if token is None else None,
                "responseObject": [
                    {
                        "jobCode": f"GDN-{suffix}",
                        "jobName": f"Software Engineer {suffix}",
                        "active": True,
                        "postingStatus": True,
                        "recruitmentStatus": "Open",
                    }
                ],
            },
        )

    jobs = BlibliScraper(client=client(handler)).extract_jobs(
        "https://careers.blibli.com/jobs"
    )

    assert tokens == [None, "page-2"]
    assert [job.id for job in jobs] == ["GDN-1", "GDN-2"]


def test_bytedance_fetches_every_offset_until_count() -> None:
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["limit"] == 1000
        offset = body["offset"]
        offsets.append(offset)
        records = (
            [{"id": "1", "title": "AI Engineer"}, {"id": "2", "title": "ML Engineer"}]
            if offset == 0
            else [{"id": "3", "title": "LLM Engineer"}]
        )
        return httpx.Response(
            200,
            json={"code": 0, "data": {"job_post_list": records, "count": 3}},
        )

    jobs = ByteDanceScraper(client=client(handler)).extract_jobs(
        "https://joinbytedance.com/search?keyword=engineer"
    )

    assert offsets == [0, 2]
    assert [job.id for job in jobs] == ["1", "2", "3"]


def test_bytedance_recovers_missing_unique_jobs_across_bounded_passes() -> None:
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["limit"] == 1000
        offset = body["offset"]
        offsets.append(offset)
        pass_number = (len(offsets) - 1) // 2
        if offset == 0:
            records = [
                {"id": "1", "title": "Backend Engineer"},
                {"id": "2", "title": "Software Engineer"},
            ]
        elif pass_number == 0:
            records = [{"id": "2", "title": "Software Engineer"}]
        else:
            records = [{"id": "3", "title": "AI Engineer"}]
        return httpx.Response(
            200,
            json={"code": 0, "data": {"job_post_list": records, "count": 3}},
        )

    jobs = ByteDanceScraper(client=client(handler)).extract_jobs(
        "https://joinbytedance.com/search?keyword=engineer"
    )

    assert offsets == [0, 2, 0, 2]
    assert [job.id for job in jobs] == ["1", "2", "3"]


def test_bytedance_raises_after_three_incomplete_passes() -> None:
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = json.loads(request.content)["offset"]
        offsets.append(offset)
        records = (
            [
                {"id": "1", "title": "Backend Engineer"},
                {"id": "2", "title": "Software Engineer"},
            ]
            if offset == 0
            else [{"id": "2", "title": "Software Engineer"}]
        )
        return httpx.Response(
            200,
            json={"code": 0, "data": {"job_post_list": records, "count": 3}},
        )

    with pytest.raises(DOMStructureChangedError, match="after 3 passes"):
        ByteDanceScraper(client=client(handler)).extract_jobs(
            "https://joinbytedance.com/search?keyword=engineer"
        )

    assert offsets == [0, 2, 0, 2, 0, 2]


def test_tiket_fetches_every_workday_offset_until_total() -> None:
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = json.loads(request.content)["offset"]
        offsets.append(offset)
        ids = ["R-1", "R-2"] if offset == 0 else ["R-3"]
        records = [
            {
                "title": f"Software Engineer {raw_id}",
                "externalPath": f"/job/Jakarta/{raw_id}",
                "bulletFields": [raw_id],
            }
            for raw_id in ids
        ]
        return httpx.Response(200, json={"total": 3, "jobPostings": records})

    jobs = TiketScraper(client=client(handler)).extract_jobs(
        "https://tiketdotcom.wd3.myworkdayjobs.com/Tiket_Careers?q=engineer"
    )

    assert offsets == [0, 2]
    assert [job.id for job in jobs] == ["R-1", "R-2", "R-3"]


def test_stockbit_fetches_all_reported_api_pages() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(page)
        ids = ["1", "2"] if page == 1 else ["3"]
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [
                    {"id": raw_id, "title": f"Backend Engineer {raw_id}"}
                    for raw_id in ids
                ],
                "meta": {"page-count": 2, "record-count": 3},
            },
        )

    jobs = StockbitScraper(client=client(handler)).extract_jobs(
        "https://careers.stockbit.com/jobs?search=engineer"
    )

    assert pages == [1, 2]
    assert [job.id for job in jobs] == ["1", "2", "3"]


def test_stockbit_rejects_a_finished_page_count_with_missing_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [{"id": "1", "title": "Backend Engineer"}],
                "meta": {"page-count": 1, "record-count": 2},
            },
        )

    with pytest.raises(DOMStructureChangedError, match="API advertised 2"):
        StockbitScraper(client=client(handler)).extract_jobs(
            "https://careers.stockbit.com/jobs?search=engineer"
        )


def test_traveloka_fetches_through_last_page() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(page)
        return httpx.Response(
            200,
            json={
                "data": {
                    "page": page,
                    "lastPage": 2,
                    "jobs": [
                        {
                            "requisitionId": f"mj{page}",
                            "title": f"Backend Engineer {page}",
                            "link": f"/jobs/mj{page}",
                        }
                    ],
                }
            },
        )

    jobs = TravelokaScraper(client=client(handler)).extract_jobs(
        "https://careers.traveloka.com/jobs?keyword=engineer"
    )

    assert pages == [1, 2]
    assert [job.id for job in jobs] == ["mj1", "mj2"]


def test_telkom_fetches_through_api_last_page() -> None:
    pages: list[int] = []

    class TestTelkomScraper(TelkomScraper):
        def _discover_public_api_auth(self, careers_url: str) -> tuple[str, str]:
            return "public", "public"

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        pages.append(page)
        return httpx.Response(
            200,
            json={
                "data": {
                    "total": 2,
                    "last_page": 2,
                    "data": [
                        {
                            "name": f"Backend Engineer {page}",
                            "short_desc_url": f"backend-engineer-{page}",
                        }
                    ],
                }
            },
        )

    jobs = TestTelkomScraper(client=client(handler)).extract_jobs(
        "https://careers.telkom.co.id/search-jobs"
    )

    assert pages == [1, 2]
    assert [job.id for job in jobs] == ["1", "2"]


def grab_page(ids: list[str], total: int, next_href: str | None) -> str:
    cards = "".join(
        f'<div class="card card-job" data-id="{raw_id}">'
        f'<h2 class="card-title"><a href="/en/jobs/{raw_id}/software-engineer-{raw_id}/">'
        f"Software Engineer {raw_id}</a></h2></div>"
        for raw_id in ids
    )
    next_link = (
        f'<a class="page-link" rel="next" href="{next_href}">Next</a>'
        if next_href
        else ""
    )
    start = 0 if not ids else 1
    return (
        f'<p class="job-count">Displaying {start} to {len(ids)} of {total} '
        f'matching jobs</p>{cards}<ul class="pagination">{next_link}</ul>'
    )


def test_grab_follows_next_page_and_reconciles_advertised_total() -> None:
    pages: list[str] = []
    user_agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        pages.append(page)
        user_agents.append(request.headers["user-agent"])
        html = (
            grab_page(["1", "2"], 3, "/en/jobs/?search=engineer&page=2")
            if page == "1"
            else grab_page(["3"], 3, None)
        )
        return httpx.Response(200, text=html)

    jobs = GrabScraper(client=client(handler)).extract_jobs(
        "https://www.grab.careers/en/jobs/?search=engineer&pagesize=20#results"
    )

    assert pages == ["1", "2"]
    assert all("Mozilla/5.0" in value for value in user_agents)
    assert [job.id for job in jobs] == ["1", "2", "3"]


def test_grab_rejects_early_finish_before_advertised_total() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=grab_page(["1"], 2, None))
    )

    with pytest.raises(DOMStructureChangedError, match="advertised 2"):
        GrabScraper(client=httpx.Client(transport=transport)).extract_jobs(
            "https://www.grab.careers/en/jobs/?search=engineer"
        )


@pytest.mark.parametrize(
    "scraper",
    [GoToScraper(), GojekScraper()],
)
def test_goto_api_family_rejects_incomplete_single_response(scraper) -> None:
    payload = {
        "code": "200000",
        "data": {
            "page": 1,
            "total": 2,
            "items": [
                {
                    "parent_department": "Engineering",
                    "job_list": [
                        {
                            "id": "1",
                            "text": "Software Engineer",
                            "state": "published",
                            "distributionChannels": ["public"],
                        }
                    ],
                }
            ],
        },
    }

    with pytest.raises(DOMStructureChangedError, match="advertised 2"):
        scraper.parse_api_payload(payload)


@pytest.mark.parametrize(
    ("scraper_class", "source_url", "company_code", "origin"),
    [
        (
            GoToScraper,
            "https://www.gotocompany.com/en/careers",
            "HoldCo",
            "https://www.gotocompany.com",
        ),
        (
            GojekScraper,
            "https://www.gojek.io/careers",
            "ODS",
            "https://www.gojek.io",
        ),
    ],
)
def test_goto_api_family_requests_the_complete_company_dataset(
    scraper_class, source_url: str, company_code: str, origin: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {
            "company": company_code,
            "search": "",
            "location": "",
            "department": "",
        }
        assert request.headers["origin"] == origin
        return httpx.Response(
            200,
            json={
                "code": "200000",
                "data": {"page": 1, "total": 0, "items": []},
            },
        )

    assert scraper_class(client=client(handler)).extract_jobs(source_url) == []
