import json
import os
import tempfile
from pathlib import Path

from models import JobPosting


class StateManager:
    def __init__(self, path: str | Path = "seen_jobs.json") -> None:
        self.path = Path(path)

    def _load(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid seen job state in {self.path}") from exc
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"Invalid seen job state in {self.path}")
        return set(value)

    def get_new_jobs(self, jobs: list[JobPosting]) -> list[JobPosting]:
        seen = self._load()
        new_jobs: list[JobPosting] = []
        discovered: set[str] = set()
        for posting in jobs:
            composite_id = posting.composite_id()
            if composite_id not in seen and composite_id not in discovered:
                new_jobs.append(posting)
                discovered.add(composite_id)
        return new_jobs

    def mark_seen(self, jobs: list[JobPosting]) -> None:
        if not jobs:
            return
        seen = self._load()
        updated = seen | {posting.composite_id() for posting in jobs}
        if updated == seen:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                json.dump(sorted(updated), temporary, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
