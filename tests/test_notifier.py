import logging

import httpx
import pytest

from models import JobAlert, JobDetails, JobPosting, JobSummary
from notifier import NotificationError, TelegramNotifier, escape_markdown


def posting(number: int = 1, title: str = "Backend Engineer") -> JobPosting:
    return JobPosting(
        id=str(number),
        title=title,
        company="Acme & Partners",
        url=f"https://example.test/jobs/{number}",
        posted_date="2026-07-25",
    )


def test_missing_environment_configuration_fails_clearly(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN.*TELEGRAM_CHAT_ID"):
        TelegramNotifier(env_file=tmp_path / "missing.env")


def test_loads_telegram_configuration_from_env_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "temporary")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "temporary")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=token-from-file\nTELEGRAM_CHAT_ID=chat-from-file\n",
        encoding="utf-8",
    )

    notifier = TelegramNotifier(
        env_file=env_file,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: None)),
    )

    assert notifier.token == "token-from-file"
    assert notifier.chat_id == "chat-from-file"


def test_existing_environment_values_override_env_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-from-environment")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-from-environment")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=token-from-file\nTELEGRAM_CHAT_ID=chat-from-file\n",
        encoding="utf-8",
    )

    notifier = TelegramNotifier(
        env_file=env_file,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: None)),
    )

    assert notifier.token == "token-from-environment"
    assert notifier.chat_id == "chat-from-environment"


def test_escape_markdown_v2_reserved_characters() -> None:
    assert escape_markdown("A_B [team]!") == r"A\_B \[team\]\!"
    assert escape_markdown(r"C:\Temp\_cache") == r"C:\\Temp\\\_cache"


def test_send_job_alerts_posts_markdown_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier(
        token="secret-token",
        chat_id="123",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    notifier.send_job_alerts([posting()])

    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.telegram.org/botsecret-token/sendMessage"
    payload = __import__("json").loads(requests[0].content)
    assert payload["chat_id"] == "123"
    assert payload["parse_mode"] == "MarkdownV2"
    assert payload["disable_web_page_preview"] is True
    assert "*New Engineering Jobs*" in payload["text"]
    assert "Acme & Partners" in payload["text"]
    assert "[Backend Engineer](https://example.test/jobs/1)" in payload["text"]
    assert "2026\\-07\\-25" in payload["text"]


def test_empty_job_list_is_a_noop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Telegram must not be called")

    notifier = TelegramNotifier(
        token="token",
        chat_id="chat",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    notifier.send_job_alerts([])


def test_long_job_list_is_chunked_below_telegram_limit() -> None:
    message_lengths: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        message_lengths.append(len(payload["text"]))
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier(
        token="token",
        chat_id="chat",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    jobs = [posting(number, f"Backend Engineer {'x' * 180} {number}") for number in range(50)]

    notifier.send_job_alerts(jobs)

    assert len(message_lengths) > 1
    assert all(length <= 4096 for length in message_lengths)


def test_error_alert_identifies_company_and_escapes_details() -> None:
    messages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        messages.append(__import__("json").loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier(
        token="token",
        chat_id="chat",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    notifier.send_error_alert("Acme [ID]", "selector .job_card missing!")

    assert "Scraper Maintenance Required" in messages[0]
    assert r"Acme \[ID\]" in messages[0]
    assert r"\.job\_card missing\!" in messages[0]


def test_summary_alert_contains_compact_role_details() -> None:
    messages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        messages.append(__import__("json").loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    alert = JobAlert(
        posting=posting(),
        details=JobDetails(description_text="Full description", location="Jakarta"),
        summary=JobSummary(
            overview="Build reliable payment services.",
            responsibilities=["Design backend APIs", "Operate production services"],
            requirements=["Five years backend experience"],
            tech_stack=["Python", "PostgreSQL"],
            experience="5+ years",
            location="Jakarta",
            work_arrangement="Hybrid",
            employment_type="Full-time",
            salary="IDR 30-40 million",
        ),
    )
    notifier = TelegramNotifier(
        token="token",
        chat_id="chat",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    notifier.send_job_alerts([alert])

    message = messages[0]
    assert "Build reliable payment services\\." in message
    assert "*Responsibilities*" in message
    assert "• Design backend APIs" in message
    assert "*Requirements*" in message
    assert "Python, PostgreSQL" in message
    assert "Jakarta · Hybrid · Full\\-time" in message
    assert "5\\+ years" in message
    assert "IDR 30\\-40 million" in message


def test_extracted_metadata_takes_precedence_over_conflicting_ai_values() -> None:
    messages: list[str] = []
    notifier = TelegramNotifier(
        token="token",
        chat_id="chat",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (
                    messages.append(__import__("json").loads(request.content)["text"])
                    or httpx.Response(200, json={"ok": True})
                )
            )
        ),
    )
    notifier.send_job_alerts(
        [
            JobAlert(
                posting=posting(),
                details=JobDetails(
                    description_text="Full description",
                    location="Jakarta",
                    work_arrangement="Hybrid",
                    employment_type="Full-time",
                    salary="IDR 30-40 million",
                ),
                summary=JobSummary(
                    overview="Build services.",
                    location="Singapore",
                    work_arrangement="On-site",
                    employment_type="Contract",
                    salary="SGD 1",
                ),
            )
        ]
    )

    message = messages[0]
    assert "Jakarta · Hybrid · Full\\-time" in message
    assert "IDR 30\\-40 million" in message
    assert "Singapore" not in message
    assert "On\\-site" not in message
    assert "Contract" not in message
    assert "SGD 1" not in message


def test_summary_unavailable_alert_keeps_the_job_link() -> None:
    messages: list[str] = []
    notifier = TelegramNotifier(
        token="token",
        chat_id="chat",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (
                    messages.append(__import__("json").loads(request.content)["text"])
                    or httpx.Response(200, json={"ok": True})
                )
            )
        ),
    )

    notifier.send_job_alerts(
        [JobAlert(posting=posting(), summary_unavailable=True)]
    )

    assert "Summary unavailable" in messages[0]
    assert "https://example.test/jobs/1" in messages[0]


def test_maximum_valid_summary_still_produces_telegram_sized_messages() -> None:
    message_lengths: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        message_lengths.append(len(payload["text"]))
        return httpx.Response(200, json={"ok": True})

    punctuation = "_" * 160
    alert = JobAlert(
        posting=posting(),
        summary=JobSummary(
            overview="." * 280,
            responsibilities=[punctuation] * 3,
            requirements=[punctuation] * 3,
            tech_stack=[punctuation] * 8,
            experience=punctuation,
            location=punctuation,
            work_arrangement="_" * 80,
            employment_type="_" * 80,
            salary=punctuation,
        ),
    )
    notifier = TelegramNotifier(
        token="token",
        chat_id="chat",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    notifier.send_job_alerts([alert])

    assert message_lengths
    assert all(length <= 4096 for length in message_lengths)


def test_http_failure_raises_safe_error_without_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"ok": False})

    notifier = TelegramNotifier(
        token="super-secret-token",
        chat_id="chat",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(NotificationError) as raised:
        notifier.send_job_alerts([posting()])

    assert "super-secret-token" not in str(raised.value)


def test_bot_token_is_never_exposed_in_http_info_logs(caplog) -> None:
    token = "super-secret-token-for-log-test"
    notifier = TelegramNotifier(
        token=token,
        chat_id="chat",
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"ok": True})
            )
        ),
    )

    with caplog.at_level(logging.INFO, logger="httpx"):
        notifier.send_job_alerts([posting()])

    assert token not in caplog.text


def test_ok_false_response_raises_notification_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": False, "description": "Bad Request"})
    )
    notifier = TelegramNotifier(
        token="token",
        chat_id="chat",
        client=httpx.Client(transport=transport),
    )

    with pytest.raises(NotificationError, match="Bad Request"):
        notifier.send_job_alerts([posting()])
