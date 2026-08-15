from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class LocalModelRegistry:
    def __init__(self, state_root: Path) -> None:
        self.registry_path = state_root / "models.json"
        self.current_path = state_root / "current-model.json"

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return dict(json.loads(path.read_text(encoding="utf-8")))

    def models(self) -> list[dict[str, Any]]:
        payload = self._read(self.registry_path) or {"models": []}
        return list(payload.get("models", []))

    def current(self) -> dict[str, Any] | None:
        return self._read(self.current_path)
