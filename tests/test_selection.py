from __future__ import annotations

import pytest
from model_manager.errors import AmbiguousSelection, NoGGUFFiles
from model_manager.models import RemoteFile
from model_manager.selection import group_gguf_files, select_choice


def remote(path: str, size: int = 100) -> RemoteFile:
    return RemoteFile(path=path, size=size)


def test_single_usable_file_is_selected() -> None:
    choices = group_gguf_files([remote("model.gguf"), remote("mmproj-model.gguf")])
    choice, pattern = select_choice(choices, explicit_pattern=None, default_pattern=None)
    assert choice.primary.path == "model.gguf"
    assert pattern is None


def test_single_usable_file_is_not_rejected_by_default_pattern() -> None:
    choices = group_gguf_files([remote("only-model-Q8_0.gguf")])
    choice, pattern = select_choice(
        choices,
        explicit_pattern=None,
        default_pattern="*Q4_K_M*.gguf",
    )
    assert choice.primary.path == "only-model-Q8_0.gguf"
    assert pattern is None


def test_default_pattern_selects_one_quantization() -> None:
    choices = group_gguf_files([remote("model-Q4_K_M.gguf"), remote("model-Q8_0.gguf")])
    choice, pattern = select_choice(choices, explicit_pattern=None, default_pattern="*Q4_K_M*.gguf")
    assert choice.primary.path == "model-Q4_K_M.gguf"
    assert pattern == "*Q4_K_M*.gguf"


def test_ambiguous_selection_lists_choices() -> None:
    choices = group_gguf_files([remote("a.gguf"), remote("b.gguf")])
    with pytest.raises(AmbiguousSelection) as error:
        select_choice(choices, explicit_pattern=None, default_pattern=None)
    assert len(error.value.choices) == 2


def test_shards_are_one_complete_choice() -> None:
    choices = group_gguf_files(
        [
            remote("model-Q4_K_M-00002-of-00002.gguf", 60),
            remote("model-Q4_K_M-00001-of-00002.gguf", 40),
        ]
    )
    assert len(choices) == 1
    assert choices[0].primary.path.endswith("00001-of-00002.gguf")
    assert choices[0].total_size == 100


def test_incomplete_shards_are_rejected() -> None:
    with pytest.raises(NoGGUFFiles, match="incomplete"):
        group_gguf_files([remote("model-00001-of-00002.gguf")])
