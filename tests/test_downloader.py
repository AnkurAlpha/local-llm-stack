from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from model_manager.downloader import Downloader, has_gguf_magic, safe_join
from model_manager.errors import IntegrityError, UnsafePath
from model_manager.huggingface import RepositorySnapshot
from model_manager.models import RemoteFile


@pytest.mark.parametrize("unsafe", ["../model.gguf", "/model.gguf", "a/../../model.gguf"])
def test_safe_join_rejects_path_traversal(tmp_path: Path, unsafe: str) -> None:
    with pytest.raises(UnsafePath):
        safe_join(tmp_path, unsafe)


def test_validation_requires_size_and_magic(tmp_path: Path) -> None:
    path = tmp_path / "model.gguf.partial"
    path.write_bytes(b"GGUFpayload")
    Downloader.validate_complete(path, len(b"GGUFpayload"))
    assert has_gguf_magic(path)
    with pytest.raises(IntegrityError, match="Size mismatch"):
        Downloader.validate_complete(path, 999)


def test_invalid_partial_is_never_promoted(tmp_path: Path) -> None:
    path = tmp_path / "bad.gguf.partial"
    path.write_bytes(b"HTML error page")
    with pytest.raises(IntegrityError, match="Invalid GGUF"):
        Downloader.validate_complete(path, None)
    assert path.exists()
    assert not (tmp_path / "bad.gguf").exists()


def test_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    payload = b"GGUFpayload"
    path = tmp_path / "model.gguf.partial"
    path.write_bytes(payload)
    Downloader.validate_complete(path, len(payload), hashlib.sha256(payload).hexdigest())
    with pytest.raises(IntegrityError, match="SHA-256 mismatch"):
        Downloader.validate_complete(path, len(payload), "0" * 64)


def test_huggingface_fallback_stages_on_the_model_filesystem(tmp_path: Path, monkeypatch) -> None:
    partial = tmp_path / "model.gguf.partial"
    captured: dict[str, Path] = {}

    def fake_hf_download(**kwargs):
        local_dir = Path(kwargs["local_dir"])
        captured["local_dir"] = local_dir
        downloaded = local_dir / kwargs["filename"]
        downloaded.parent.mkdir(parents=True)
        downloaded.write_bytes(b"GGUFpayload")
        return str(downloaded)

    monkeypatch.setattr("model_manager.downloader.hf_hub_download", fake_hf_download)
    snapshot = RepositorySnapshot("owner/repo", "commit", (RemoteFile("model.gguf"),))
    Downloader()._huggingface_fallback(snapshot, snapshot.files[0], partial)

    assert captured["local_dir"] == tmp_path / ".hf-downloads"
    assert partial.read_bytes() == b"GGUFpayload"
