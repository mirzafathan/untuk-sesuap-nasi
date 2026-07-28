import json
from pathlib import Path

import pytest

from models import JobPosting
from state_manager import StateManager


def job(raw_id: str, title: str = "Backend Engineer") -> JobPosting:
    return JobPosting(
        id=raw_id,
        title=title,
        company="Acme",
        url=f"https://example.test/jobs/{raw_id}",
    )


def test_missing_state_file_treats_every_unique_job_as_new(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "seen_jobs.json")
    first = job("1")

    assert manager.get_new_jobs([first, first, job("2")]) == [first, job("2")]
    assert not manager.path.exists()


def test_existing_composite_ids_are_not_returned(tmp_path: Path) -> None:
    existing = job("1")
    path = tmp_path / "seen_jobs.json"
    path.write_text(json.dumps([existing.composite_id()]), encoding="utf-8")
    manager = StateManager(path)

    assert manager.get_new_jobs([existing, job("2")]) == [job("2")]


def test_exact_title_or_url_change_is_new(tmp_path: Path) -> None:
    original = job("1")
    path = tmp_path / "seen_jobs.json"
    path.write_text(json.dumps([original.composite_id()]), encoding="utf-8")
    changed = original.model_copy(update={"title": "Senior Backend Engineer"})

    assert StateManager(path).get_new_jobs([changed]) == [changed]


def test_mark_seen_merges_and_writes_sorted_deterministic_json(tmp_path: Path) -> None:
    path = tmp_path / "seen_jobs.json"
    first, second = job("1"), job("2")
    path.write_text(json.dumps([second.composite_id()]), encoding="utf-8")
    manager = StateManager(path)

    manager.mark_seen([first, second])

    assert json.loads(path.read_text(encoding="utf-8")) == sorted(
        [first.composite_id(), second.composite_id()]
    )
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert list(tmp_path.glob(".seen_jobs.json.*.tmp")) == []


def test_mark_seen_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "seen_jobs.json"
    manager = StateManager(path)
    posting = job("1")

    manager.mark_seen([posting])
    first_contents = path.read_text(encoding="utf-8")
    manager.mark_seen([posting])

    assert path.read_text(encoding="utf-8") == first_contents


@pytest.mark.parametrize("contents", ["not json", "{}", '["ok", 123]'])
def test_malformed_state_is_never_silently_replaced(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "seen_jobs.json"
    path.write_text(contents, encoding="utf-8")
    manager = StateManager(path)

    with pytest.raises(ValueError, match="seen job state"):
        manager.get_new_jobs([job("1")])
    assert path.read_text(encoding="utf-8") == contents


def test_marking_no_jobs_does_not_create_state_file(tmp_path: Path) -> None:
    manager = StateManager(tmp_path / "seen_jobs.json")

    manager.mark_seen([])

    assert not manager.path.exists()
