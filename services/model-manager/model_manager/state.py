from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .downloader import has_gguf_magic, safe_join
from .errors import IntegrityError
from .models import ModelRecord


class CurrentModelState:
    def __init__(self, state_root: Path, models_root: Path) -> None:
        self.state_root = state_root
        self.models_root = models_root
        self.path = state_root / "current-model.json"
        state_root.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def select(self, record: ModelRecord) -> None:
        if not record.completed:
            raise IntegrityError("Cannot select an incomplete model record.")
        for model_file in record.files:
            path = safe_join(self.models_root, model_file.path)
            if not path.is_file() or path.stat().st_size != model_file.size or not has_gguf_magic(path):
                raise IntegrityError(f"Installed model file is missing or invalid: {model_file.path}")
        payload = {
            "version": 1,
            "model_id": record.model_id,
            "repo_id": record.repo_id,
            "revision": record.revision,
            "primary_file": record.primary_file,
            "files": [item.path for item in record.files],
        }
        fd, name = tempfile.mkstemp(prefix="current-model.", suffix=".tmp", dir=self.state_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
