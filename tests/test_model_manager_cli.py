from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from model_manager.models import ModelFile, ModelRecord
from model_manager.registry import Registry
from model_manager.state import CurrentModelState

ROOT = Path(__file__).resolve().parents[1]


def run_cli(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "MODELS_ROOT": str(tmp_path / "models"),
            "STATE_ROOT": str(tmp_path / "state"),
            "PYTHONPATH": str(ROOT / "services" / "model-manager"),
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "model_manager.cli", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_empty_cli_lifecycle(tmp_path: Path) -> None:
    health = run_cli(tmp_path, "health")
    assert health.returncode == 0
    assert health.stdout.strip() == "ok"
    assert run_cli(tmp_path, "list").stdout.strip() == "No completed models installed."
    assert run_cli(tmp_path, "current").stdout.strip() == "No model selected."


def test_cli_rejects_invalid_repository_before_network_access(tmp_path: Path) -> None:
    result = run_cli(tmp_path, "inspect-repo", "invalid-repo")
    assert result.returncode == 2
    assert "OWNER/REPO" in result.stderr


def test_cli_clears_a_selected_record_when_its_file_was_deleted(tmp_path: Path) -> None:
    model_path = tmp_path / "models" / "owner" / "repo" / "model.gguf"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"GGUFtest")
    record = ModelRecord(
        model_id="owner--repo--model.gguf",
        repo_id="owner/repo",
        revision="abc123",
        primary_file="owner/repo/model.gguf",
        files=(ModelFile(path="owner/repo/model.gguf", size=8),),
        total_size=8,
        downloaded_at=datetime.now(UTC).isoformat(),
        completed=True,
    )
    Registry(tmp_path / "state").upsert(record)
    state = CurrentModelState(tmp_path / "state", tmp_path / "models")
    state.select(record)
    model_path.unlink()

    result = run_cli(tmp_path, "remove", record.model_id, "--yes")
    assert result.returncode == 0
    assert "cleared the stale selection" in result.stdout
    assert state.read() is None
    assert Registry(tmp_path / "state").list() == []
