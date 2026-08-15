from __future__ import annotations

import pytest
from model_manager.huggingface import HuggingFaceRepository
from model_manager.selection import group_gguf_files


@pytest.mark.live
def test_public_huggingface_gguf_metadata_resolution() -> None:
    repository = HuggingFaceRepository()
    snapshot = repository.inspect("ggml-org/Qwen3.5-0.8B-GGUF")
    assert snapshot.revision
    choices = group_gguf_files(list(snapshot.files))
    target = repository.resolve(snapshot, choices[0].primary)
    assert target.url.startswith("https://")
    assert target.size and target.size > 0
