from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "crawler.yml"
ENV_EXAMPLE = Path(__file__).parents[1] / ".env.example"


def test_workflow_has_required_triggers_runtime_and_state_commit() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'cron: "0 8 * * *"' in text
    assert "workflow_dispatch:" in text
    assert "python-version: \"3.11\"" in text
    assert "python -m pytest" in text
    assert "python main.py" in text
    assert text.index("python -m pytest") < text.index("python main.py")
    assert "TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}" in text
    assert "TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}" in text
    assert "OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}" in text
    assert "OPENROUTER_MODEL: qwen/qwen3-30b-a3b-instruct-2507" in text
    assert 'OPENROUTER_MAX_SUMMARIES_PER_RUN: "25"' in text
    assert "RUN_OPENROUTER_INTEGRATION" not in text
    assert "stefanzweifel/git-auto-commit-action@v5" in text
    assert "file_pattern: seen_jobs.json" in text
    assert "contents: write" in text
    assert "cancel-in-progress: false" in text


def test_env_example_documents_safe_local_ai_configuration() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "OPENROUTER_API_KEY=your-openrouter-api-key" in text
    assert "OPENROUTER_MODEL=qwen/qwen3-30b-a3b-instruct-2507" in text
    assert "OPENROUTER_MAX_SUMMARIES_PER_RUN=25" in text
    assert "# RUN_OPENROUTER_INTEGRATION=1" in text
