# Daily Engineering Job Crawler

A Python 3.11+ crawler that checks 14 company career sites, filters engineering titles, summarizes new postings through OpenRouter, sends compact alerts to Telegram, and stores their composite IDs in `seen_jobs.json`.

## Monitored roles

Only titles containing one of these roles or an unambiguous spelling variant are included, using case-insensitive partial matching:

- Backend Engineer
- Software Engineer
- Fullstack Engineer
- AI Engineer
- ML Engineer
- LLM Engineer

Common equivalents are supported: `Machine Learning Engineer` matches `ML Engineer`; `Full Stack Engineer` and `Full-Stack Engineer` match `Fullstack Engineer`; and `Back End Engineer`, `Artificial Intelligence Engineer`, and `Large Language Model Engineer` match their compact forms. Role changes are not inferred, so `Machine Learning Scientist` and `Full Stack Developer` remain excluded.

## How it works

Each scraper retrieves either server-rendered HTML or the same public HTTP data source used by the company's browser application. Beautiful Soup parsers are kept for every supplied outerHTML contract. Missing containers, malformed job nodes, nonzero counts without job cards, and changed API schemas raise `DOMStructureChangedError`.

Paginated sources are not considered complete until their final page is fetched. Depending on the provider, the crawler follows offsets, page numbers, Workday totals, continuation tokens, or server-side paginator metadata. It reconciles unique raw job IDs against advertised totals where the source provides one; an empty intermediate page, repeated page/token, changing total, or final count mismatch raises `DOMStructureChangedError` instead of silently accepting a partial crawl. StaffAny, Amartha, DANA, GoTo, and Gojek currently expose complete, non-paginated datasets in a single response. Gojek's visible page buttons slice that complete API response in the browser, so no server records are omitted.

`main.py` handles scraper failures independently. A DOM structure failure sends a Telegram maintenance alert naming the company, while the remaining companies continue.

A posting is considered unique by the SHA-256 hash of its exact:

```text
Company | Job Title | URL
```

State is written atomically and only after every job-alert message has been accepted by Telegram. If notification fails, state remains unchanged, so the next run retries the alert instead of silently skipping it.

## AI summaries

Only unseen jobs are sent through the detail-extraction and AI-summary stages. Already-seen jobs make no detail-page or OpenRouter requests, which keeps daily inference cost proportional to genuinely new postings.

For each new job, the company scraper retrieves the complete job description and explicit metadata available from the source. OpenRouter then returns a strict JSON-schema response that is validated before Python formats the Telegram message. The alert can include:

- a one-sentence role overview;
- up to three responsibilities and three requirements;
- the stated technology stack and experience level;
- location, work arrangement, employment type, and salary when explicitly provided; and
- the original job title, posting date, and application link.

The default model is `qwen/qwen3-30b-a3b-instruct-2507`, with temperature zero, a 350-token output limit, and Zero Data Retention routing requested from OpenRouter. Job-description input is treated as untrusted text and delimited from the system instructions. Long descriptions use a 16,000-character head-and-tail window so both the opening context and end-positioned requirements survive; company and title metadata are bounded separately. The model is instructed not to infer missing facts.

`OPENROUTER_MAX_SUMMARIES_PER_RUN` defaults to `25`. Once that cap is reached, remaining new jobs are still sent with their title and link plus `Summary unavailable`; no jobs are hidden. Fallback alerts count as delivered and are recorded in `seen_jobs.json`, so they are not later duplicated solely to retry a summary. The same fallback is used when the OpenRouter key is absent, a detail request temporarily fails, the provider rejects a request, or model output fails validation. A changed detail-page/API contract also triggers one scraper-maintenance alert for that company during the run.

## Local setup

Create and activate a virtual environment, then install the project and test dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Copy the safe example file and edit the ignored local `.env` file:

```bash
cp .env.example .env
chmod 600 .env
```

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id
OPENROUTER_API_KEY=your-openrouter-api-key
OPENROUTER_MODEL=qwen/qwen3-30b-a3b-instruct-2507
OPENROUTER_MAX_SUMMARIES_PER_RUN=25
```

The OpenRouter key is optional at runtime: without it, the crawler sends the fallback title-and-link alerts. The included `.env.example` is safe to commit, while `.env` is excluded by `.gitignore` and must never be committed. `chmod 600` limits the populated file to your local account on Unix-like systems. Existing process environment variables take precedence over `.env`, so GitHub Actions continues to use repository secrets.

Run all tests:

```bash
python -m pytest
```

The normal test suite uses mocked HTTP responses and does not need an OpenRouter key or spend any model credits. It covers the strict request schema, Qwen model selection, input truncation, prompt-injection isolation, response validation, retries, credential redaction, crawler fallbacks, summary limits, and Telegram formatting.

After the mocked suite passes, an optional live smoke test can run one OpenRouter summary invocation using the credentials in `.env`:

```bash
RUN_OPENROUTER_INTEGRATION=1 python -m pytest tests/test_openrouter_integration.py -v
```

That command normally makes one billable API call; the client may retry once after a network timeout, HTTP 408/429, or server error, honoring a numeric `Retry-After` delay up to 30 seconds. Without `RUN_OPENROUTER_INTEGRATION=1`, the live test is skipped. It does not contact Telegram or modify `seen_jobs.json`.

Run the crawler:

```bash
python main.py
```

The first successful run alerts every currently matching job because `seen_jobs.json` initially contains an empty JSON array. Later runs alert only unseen composite IDs.

For a manual end-to-end check, run `python main.py`, confirm that Telegram receives summarized alerts for up to the configured cap and fallback alerts for any remaining new jobs, then run it a second time. The second run should send no duplicate alerts and should make no detail or OpenRouter calls for those already-seen jobs. A clean `seen_jobs.json` can produce many first-run messages; do not reset it again after a successful production run unless duplicate alerts are intentional.

A summarized Telegram entry follows this shape, omitting fields the source did not state:

```text
Company
Job title and application link
📍 Location · Hybrid · Full-time
🧭 One-sentence overview

Responsibilities
• Up to three items

Requirements
• Up to three items

🛠 Technology stack
💼 Experience
💰 Salary
Posted date
```

## GitHub deployment

1. Push this project to a GitHub repository whose primary branch is named `main`.
2. In **Settings → Secrets and variables → Actions**, create repository secrets named `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `OPENROUTER_API_KEY`.
3. In **Settings → Actions → General**, ensure workflow permissions allow read and write access, unless repository policy already grants the workflow's declared `contents: write` permission.
4. Open **Actions → Daily engineering job crawler** and run `workflow_dispatch` once to verify the secrets and initial notification.

The workflow runs every day at 08:00 UTC, runs the mocked test suite before crawling, and commits only `seen_jobs.json` through `stefanzweifel/git-auto-commit-action@v5`. Automated tests do not receive the OpenRouter secret and the opt-in live-test flag is not enabled, so the test step cannot consume model credits. The crawl step uses the approved Qwen model and a 25-summary-per-run cap. The concurrency setting prevents scheduled and manual runs from updating state simultaneously.

To change the production model or cap, edit `OPENROUTER_MODEL` or `OPENROUTER_MAX_SUMMARIES_PER_RUN` in `.github/workflows/crawler.yml`. Keep `OPENROUTER_API_KEY` in GitHub Secrets rather than writing it into the workflow.

Branch protection rules must permit the GitHub Actions token to push the state commit to `main`.

## Maintenance

Saved browser fragments live in `inspect-elements-outerhtml/`, their parser contract tests live in `tests/test_scrapers.py`, detail-extraction contracts live in the `tests/test_job_details_*.py` files, and end-to-end pagination contracts live in `tests/test_pagination.py`. When a maintenance alert arrives:

1. Capture fresh outerHTML or inspect the site's current public data response.
2. Add or update the failing fixture test first.
3. Update only that scraper's selectors or payload mapping.
4. Run `python -m pytest` before committing.

Malformed `seen_jobs.json` is never silently replaced. Repair or restore it as a JSON array of composite-ID strings so accidental state corruption cannot suppress notifications.
