from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from model_manager.errors import IntegrityError, ModelNotFound
from model_manager.models import ModelFile, ModelRecord
from model_manager.registry import Registry
from model_manager.state import CurrentModelState


def record(path: str = "owner/repo/model.gguf") -> ModelRecord:
    return ModelRecord(
        model_id="owner--repo--model.gguf",
        repo_id="owner/repo",
        revision="abc123",
        primary_file=path,
        files=(ModelFile(path=path, size=8),),
        total_size=8,
        downloaded_at=datetime.now(UTC).isoformat(),
        completed=True,
        pattern="*Q4*.gguf",
    )


def test_registry_round_trip_and_resolution(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "state")
    value = record()
    registry.upsert(value)
    assert registry.resolve("model.gguf") == value
    payload = json.loads((tmp_path / "state" / "models.json").read_text())
    assert payload["version"] == 1
    assert payload["models"][0]["completed"] is True
    assert registry.remove(value.model_id) == value
    with pytest.raises(ModelNotFound):
        registry.resolve(value.model_id)


def test_state_selection_validates_real_gguf(tmp_path: Path) -> None:
    models = tmp_path / "models"
    state = tmp_path / "state"
    path = models / "owner" / "repo" / "model.gguf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"GGUFtest")
    manager = CurrentModelState(state, models)
    manager.select(record())
    assert manager.read()["primary_file"] == "owner/repo/model.gguf"


def test_state_rejects_deleted_file(tmp_path: Path) -> None:
    manager = CurrentModelState(tmp_path / "state", tmp_path / "models")
    with pytest.raises(IntegrityError, match="missing or invalid"):
        manager.select(record())


def test_state_can_clear_a_stale_selection(tmp_path: Path) -> None:
    models = tmp_path / "models"
    state = tmp_path / "state"
    path = models / "owner" / "repo" / "model.gguf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"GGUFtest")
    manager = CurrentModelState(state, models)
    manager.select(record())
    manager.clear()
    assert manager.read() is None
