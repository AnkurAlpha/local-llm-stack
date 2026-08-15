from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import ModelNotFound
from .models import ModelRecord


class Registry:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self.path = state_root / "models.json"
        self.lock_path = state_root / ".model-manager.lock"
        state_root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def lock(self) -> Iterator[None]:
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> list[ModelRecord]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("models"), list):
            raise ValueError("unsupported or malformed model registry")
        return [ModelRecord.from_dict(item) for item in payload["models"]]

    def list(self) -> list[ModelRecord]:
        with self.lock():
            return self._read_unlocked()

    def _write_unlocked(self, records: list[ModelRecord]) -> None:
        payload = {"version": 1, "models": [record.to_dict() for record in records]}
        fd, name = tempfile.mkstemp(prefix="models.", suffix=".tmp", dir=self.state_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def upsert(self, record: ModelRecord) -> None:
        with self.lock():
            records = [item for item in self._read_unlocked() if item.model_id != record.model_id]
            records.append(record)
            records.sort(key=lambda item: item.model_id.lower())
            self._write_unlocked(records)

    def remove(self, model_id: str) -> ModelRecord:
        with self.lock():
            records = self._read_unlocked()
            matches = [item for item in records if item.model_id == model_id]
            if not matches:
                raise ModelNotFound(f"Model not found: {model_id}")
            self._write_unlocked([item for item in records if item.model_id != model_id])
            return matches[0]

    def resolve(self, query: str) -> ModelRecord:
        query = query.strip()
        records = self.list()
        exact = [
            item
            for item in records
            if query in {item.model_id, item.repo_id, Path(item.primary_file).name, item.primary_file}
        ]
        if len(exact) == 1:
            return exact[0]
        lowered = query.lower()
        fuzzy = [
            item
            for item in records
            if lowered in item.model_id.lower()
            or lowered in item.repo_id.lower()
            or lowered in Path(item.primary_file).name.lower()
        ]
        matches = exact or fuzzy
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ModelNotFound(f"No installed model matches: {query}")
        choices = ", ".join(item.model_id for item in matches)
        raise ModelNotFound(f"Model name is ambiguous: {query}. Matches: {choices}")
