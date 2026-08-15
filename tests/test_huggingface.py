from __future__ import annotations

from dataclasses import dataclass

import pytest
from model_manager.errors import InvalidRepository
from model_manager.huggingface import HuggingFaceRepository


@dataclass
class LFS:
    size: int
    sha256: str


@dataclass
class Sibling:
    rfilename: str
    size: int | None = None
    lfs: LFS | None = None


@dataclass
class Info:
    sha: str
    siblings: list[Sibling]


class FakeAPI:
    def model_info(self, **_: object) -> Info:
        return Info("commit123", [Sibling("model-Q4_K_M.gguf", lfs=LFS(42, "a" * 64))])


def test_repository_metadata_is_normalized() -> None:
    snapshot = HuggingFaceRepository(api=FakeAPI()).inspect("owner/repository")
    assert snapshot.revision == "commit123"
    assert snapshot.files[0].size == 42
    assert snapshot.files[0].sha256 == "a" * 64


@pytest.mark.parametrize("repo", ["one-part", "../bad/repo", "owner/repo/extra", "/repo"])
def test_invalid_repository_ids_are_rejected(repo: str) -> None:
    with pytest.raises(InvalidRepository):
        HuggingFaceRepository.validate_repo_id(repo)
