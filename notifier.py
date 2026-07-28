import logging
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

from models import JobAlert, JobPosting


TELEGRAM_MESSAGE_LIMIT = 4096
_MARKDOWN_V2_RESERVED = re.compile(r"([_\*\[\]()~`>#+\-=|{}.!])")
_TELEGRAM_TOKEN_URL = re.compile(r"(https://api\.telegram\.org/bot)[^/\s]+")


class _TelegramTokenRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        def redact(value: object) -> object:
            rendered = str(value)
            redacted = _TELEGRAM_TOKEN_URL.sub(r"\1<redacted>", rendered)
            return redacted if redacted != rendered else value

        if isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}
        record.msg = redact(record.msg)
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(item, _TelegramTokenRedactionFilter) for item in _httpx_logger.filters):
    _httpx_logger.addFilter(_TelegramTokenRedactionFilter())


class NotificationError(RuntimeError):
    """Raised when Telegram does not accept a notification."""


def escape_markdown(value: str) -> str:
    escaped_backslashes = value.replace("\\", "\\\\")
    return _MARKDOWN_V2_RESERVED.sub(r"\\\1", escaped_backslashes)


def _escape_link_url(value: str) -> str:
    return value.replace("\\", "\\\\").replace(")", "\\)")


class TelegramNotifier:
    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        client: httpx.Client | None = None,
        env_file: str | os.PathLike[str] | None = ".env",
    ) -> None:
        if env_file is not None:
            load_dotenv(dotenv_path=Path(env_file), override=False)
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        missing = [
            name
            for name, value in (
                ("TELEGRAM_BOT_TOKEN", self.token),
                ("TELEGRAM_CHAT_ID", self.chat_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        self.client = client or httpx.Client(timeout=20.0)
        self._owns_client = client is None

    @property
    def api_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}/sendMessage"

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def send_job_alerts(self, jobs: list[JobAlert | JobPosting]) -> None:
        if not jobs:
            return
        header = "🚨 *New Engineering Jobs*"
        maximum_block_length = TELEGRAM_MESSAGE_LIMIT - len(header) - 2
        blocks = [
            self._fit_job_block(job, maximum_block_length) for job in jobs
        ]
        messages: list[str] = []
        current = header
        for block in blocks:
            candidate = f"{current}\n\n{block}"
            if len(candidate) <= TELEGRAM_MESSAGE_LIMIT:
                current = candidate
                continue
            if current == header:
                raise NotificationError("A formatted job alert exceeds Telegram's message limit")
            messages.append(current)
            current = f"{header}\n\n{block}"
            if len(current) > TELEGRAM_MESSAGE_LIMIT:
                raise NotificationError("A formatted job alert exceeds Telegram's message limit")
        messages.append(current)
        for message in messages:
            self._send(message)

    def _fit_job_block(
        self, value: JobAlert | JobPosting, maximum_length: int
    ) -> str:
        block = self._format_job(value)
        if len(block) <= maximum_length:
            return block

        alert = value if isinstance(value, JobAlert) else JobAlert(posting=value)
        job = alert.posting
        company = escape_markdown(job.company[:120])
        title = escape_markdown(job.title[:180])
        lines = [f"*{company}*", f"[{title}]({_escape_link_url(job.url)})"]
        if alert.summary is not None:
            lines.append(f"🧭 {escape_markdown(alert.summary.overview)}")
        lines.append("Summary shortened to fit Telegram")
        if job.posted_date:
            lines.append(f"Posted: {escape_markdown(job.posted_date[:100])}")
        compact = "\n".join(lines)
        if len(compact) <= maximum_length:
            return compact

        raise NotificationError("A formatted job alert exceeds Telegram's message limit")

    def send_error_alert(self, company_name: str, error_details: str) -> None:
        safe_details = error_details[:1800]
        text = (
            "🛠 *Scraper Maintenance Required*\n\n"
            f"*Company:* {escape_markdown(company_name)}\n"
            f"*Error:* {escape_markdown(safe_details)}"
        )
        self._send(text)

    def _format_job(self, value: JobAlert | JobPosting) -> str:
        alert = value if isinstance(value, JobAlert) else JobAlert(posting=value)
        job = alert.posting
        lines = [
            f"*{escape_markdown(job.company)}*",
            f"[{escape_markdown(job.title)}]({_escape_link_url(job.url)})",
        ]
        summary = alert.summary
        if summary is not None:
            metadata = [
                (alert.details.location if alert.details else None) or summary.location,
                (alert.details.work_arrangement if alert.details else None)
                or summary.work_arrangement,
                (alert.details.employment_type if alert.details else None)
                or summary.employment_type,
            ]
            visible_metadata = [item for item in metadata if item]
            if visible_metadata:
                lines.append(f"📍 {escape_markdown(' · '.join(visible_metadata))}")
            lines.append(f"🧭 {escape_markdown(summary.overview)}")
            if summary.responsibilities:
                lines.append("*Responsibilities*")
                lines.extend(
                    f"• {escape_markdown(item)}" for item in summary.responsibilities
                )
            if summary.requirements:
                lines.append("*Requirements*")
                lines.extend(f"• {escape_markdown(item)}" for item in summary.requirements)
            if summary.tech_stack:
                lines.append(f"🛠 {escape_markdown(', '.join(summary.tech_stack))}")
            if summary.experience:
                lines.append(f"💼 {escape_markdown(summary.experience)}")
            salary = (alert.details.salary if alert.details else None) or summary.salary
            if salary:
                lines.append(f"💰 {escape_markdown(salary)}")
        elif alert.summary_unavailable:
            lines.append("Summary unavailable")
        if job.posted_date:
            lines.append(f"Posted: {escape_markdown(job.posted_date)}")
        return "\n".join(lines)

    def _send(self, text: str) -> None:
        try:
            response = self.client.post(
                self.api_url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            status = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None
                else "network"
            )
            raise NotificationError(f"Telegram request failed ({status})") from None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            description = payload.get("description", "unknown Telegram error") if isinstance(payload, dict) else "invalid Telegram response"
            raise NotificationError(f"Telegram rejected the notification: {description}")
