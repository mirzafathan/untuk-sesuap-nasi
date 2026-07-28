import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from exceptions import (
    DOMStructureChangedError,
    JobDetailStructureChangedError,
    SummarizationError,
)
from models import JobAlert, JobPosting
from notifier import TelegramNotifier
from scrapers import (
    AmarthaScraper,
    BlibliScraper,
    ByteDanceScraper,
    DanaScraper,
    GojekScraper,
    GoToScraper,
    GrabScraper,
    MekariScraper,
    ShopeeScraper,
    StaffanyScraper,
    StockbitScraper,
    TelkomScraper,
    TiketScraper,
    TravelokaScraper,
)
from scrapers.base import BaseScraper
from state_manager import StateManager
from summarizer import JobSummarizer, OpenRouterSummarizer


logger = logging.getLogger(__name__)
_AUTO_SUMMARIZER = object()


@dataclass(frozen=True)
class ScraperTarget:
    name: str
    url: str
    factory: Callable[[], BaseScraper]


TARGETS: tuple[ScraperTarget, ...] = (
    ScraperTarget(
        "Shopee",
        "https://careers.shopee.co.id/jobs?name=engineer&limit=50&offset=0",
        ShopeeScraper,
    ),
    ScraperTarget(
        "Mekari",
        "https://mekari.hire.trakstar.com/?q=engineer&limit=25",
        MekariScraper,
    ),
    ScraperTarget("Blibli", "https://careers.blibli.com/jobs?title=", BlibliScraper),
    ScraperTarget("StaffAny", "https://www.staffany.com/careers/#eng", StaffanyScraper),
    ScraperTarget("Amartha", "https://apply.workable.com/amartha/", AmarthaScraper),
    ScraperTarget(
        "ByteDance",
        "https://joinbytedance.com/search?keyword=engineer&recruitment_id_list=&job_category_id_list=&subject_id_list=&location_code_list=&limit=12&offset=0",
        ByteDanceScraper,
    ),
    ScraperTarget(
        "Tiket",
        "https://tiketdotcom.wd3.myworkdayjobs.com/Tiket_Careers?q=engineer",
        TiketScraper,
    ),
    ScraperTarget(
        "Stockbit",
        "https://careers.stockbit.com/jobs?search=engineer",
        StockbitScraper,
    ),
    ScraperTarget(
        "Traveloka",
        "https://careers.traveloka.com/jobs?keyword=engineer",
        TravelokaScraper,
    ),
    ScraperTarget("DANA", "https://www.career.dana.id/#job-list", DanaScraper),
    ScraperTarget("Telkom", "https://careers.telkom.co.id/search-jobs", TelkomScraper),
    ScraperTarget(
        "Grab",
        "https://www.grab.careers/en/jobs/?search=engineer&pagesize=20#results",
        GrabScraper,
    ),
    ScraperTarget("GoTo", "https://www.gotocompany.com/en/careers", GoToScraper),
    ScraperTarget("Gojek", "https://www.gojek.io/careers", GojekScraper),
)


def run(
    targets: Sequence[ScraperTarget] = TARGETS,
    state_manager: StateManager | None = None,
    notifier: TelegramNotifier | None = None,
    summarizer: JobSummarizer | None | object = _AUTO_SUMMARIZER,
    max_summaries_per_run: int | None = None,
) -> list[JobPosting]:
    max_summaries_per_run = _resolve_summary_limit(max_summaries_per_run)
    state = state_manager or StateManager()
    owns_notifier = notifier is None
    telegram = notifier or TelegramNotifier()
    owns_summarizer = False
    ai: JobSummarizer | None
    if summarizer is _AUTO_SUMMARIZER:
        try:
            ai = OpenRouterSummarizer()
            owns_summarizer = True
        except ValueError:
            ai = None
            logger.warning(
                "OPENROUTER_API_KEY is not configured; sending fallback alerts"
            )
    elif summarizer is None:
        ai = None
    else:
        ai = summarizer

    new_jobs: list[JobPosting] = []
    alerts: list[JobAlert] = []
    pending_ids: set[str] = set()
    detail_alerted_companies: set[str] = set()
    summary_attempts = 0
    try:
        for target in targets:
            try:
                scraper = target.factory()
            except Exception as exc:
                logger.error(
                    "%s scraper could not be initialized: %s",
                    target.name,
                    type(exc).__name__,
                )
                continue
            try:
                try:
                    jobs = scraper.extract_jobs(target.url)
                except DOMStructureChangedError as exc:
                    logger.error("%s scraper structure changed: %s", target.name, exc)
                    _send_maintenance_alert(telegram, target.name, str(exc))
                    continue
                except Exception as exc:
                    logger.error(
                        "%s scraper failed with %s: %s",
                        target.name,
                        type(exc).__name__,
                        exc,
                    )
                    continue

                logger.info("%s: found %d matching jobs", target.name, len(jobs))
                target_new_jobs = state.get_new_jobs(jobs)
                for job in target_new_jobs:
                    composite_id = job.composite_id()
                    if composite_id in pending_ids:
                        continue
                    pending_ids.add(composite_id)
                    new_jobs.append(job)

                    if ai is None or summary_attempts >= max_summaries_per_run:
                        alerts.append(
                            JobAlert(posting=job, summary_unavailable=True)
                        )
                        continue
                    try:
                        details = scraper.extract_job_details(job)
                    except JobDetailStructureChangedError as exc:
                        logger.error("%s detail structure changed: %s", target.name, exc)
                        if target.name not in detail_alerted_companies:
                            _send_maintenance_alert(telegram, target.name, str(exc))
                            detail_alerted_companies.add(target.name)
                        alerts.append(
                            JobAlert(posting=job, summary_unavailable=True)
                        )
                        continue
                    except Exception as exc:
                        logger.error(
                            "%s detail extraction failed with %s",
                            target.name,
                            type(exc).__name__,
                        )
                        alerts.append(
                            JobAlert(posting=job, summary_unavailable=True)
                        )
                        continue

                    summary_attempts += 1
                    try:
                        summary = ai.summarize(job, details)
                    except SummarizationError as exc:
                        logger.error("Could not summarize %s job %s: %s", target.name, job.id, exc)
                        alerts.append(
                            JobAlert(
                                posting=job,
                                details=details,
                                summary_unavailable=True,
                            )
                        )
                    except Exception as exc:
                        logger.error(
                            "Unexpected summarizer failure for %s job %s: %s",
                            target.name,
                            job.id,
                            type(exc).__name__,
                        )
                        alerts.append(
                            JobAlert(
                                posting=job,
                                details=details,
                                summary_unavailable=True,
                            )
                        )
                    else:
                        alerts.append(
                            JobAlert(posting=job, details=details, summary=summary)
                        )
            finally:
                try:
                    scraper.close()
                except Exception:
                    logger.warning("Could not close %s scraper client", target.name)

        if alerts:
            telegram.send_job_alerts(alerts)
            state.mark_seen(new_jobs)
        logger.info("Discovered %d new jobs", len(new_jobs))
        return new_jobs
    finally:
        if owns_summarizer and ai is not None:
            ai.close()
        if owns_notifier:
            telegram.close()


def _send_maintenance_alert(
    telegram: TelegramNotifier, company_name: str, details: str
) -> None:
    try:
        telegram.send_error_alert(company_name, details)
    except Exception as notification_error:
        logger.error(
            "Could not send %s maintenance alert: %s",
            company_name,
            type(notification_error).__name__,
        )


def _resolve_summary_limit(configured_limit: int | None) -> int:
    if configured_limit is None:
        raw_limit = os.getenv("OPENROUTER_MAX_SUMMARIES_PER_RUN", "25")
        try:
            configured_limit = int(raw_limit)
        except ValueError as exc:
            raise ValueError(
                "OPENROUTER_MAX_SUMMARIES_PER_RUN must be an integer"
            ) from exc
    if configured_limit < 0:
        raise ValueError("OPENROUTER_MAX_SUMMARIES_PER_RUN cannot be negative")
    return configured_limit


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    new_jobs = run()
    logger.info("Crawler completed successfully with %d new jobs", len(new_jobs))


if __name__ == "__main__":
    main()
