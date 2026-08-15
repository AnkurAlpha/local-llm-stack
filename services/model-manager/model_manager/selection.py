from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from pathlib import PurePosixPath

from .errors import AmbiguousSelection, NoGGUFFiles
from .models import GGUFChoice, RemoteFile

SHARD_RE = re.compile(r"^(?P<prefix>.+)-(?P<index>\d{5})-of-(?P<total>\d{5})(?P<suffix>\.gguf)$", re.I)


def _is_usable(file: RemoteFile) -> bool:
    name = PurePosixPath(file.path).name.lower()
    return name.endswith(".gguf") and "mmproj" not in name


def group_gguf_files(files: list[RemoteFile]) -> list[GGUFChoice]:
    usable = [item for item in files if _is_usable(item)]
    if not usable:
        raise NoGGUFFiles("Repository contains no usable GGUF model files (mmproj files are auxiliary).")

    groups: dict[str, list[tuple[int, int, RemoteFile]]] = defaultdict(list)
    singles: list[RemoteFile] = []
    for item in usable:
        match = SHARD_RE.match(item.path)
        if match is None:
            singles.append(item)
            continue
        key = f"{match.group('prefix')}-of-{match.group('total')}.gguf"
        groups[key].append((int(match.group("index")), int(match.group("total")), item))

    choices = [GGUFChoice(key=item.path, files=(item,), primary=item) for item in singles]
    for key, members in groups.items():
        members.sort(key=lambda value: value[0])
        expected_total = members[0][1]
        found = [index for index, total, _ in members if total == expected_total]
        if found != list(range(1, expected_total + 1)) or len(found) != len(members):
            raise NoGGUFFiles(f"Sharded GGUF set is incomplete or inconsistent: {key}")
        grouped_files = tuple(item for _, _, item in members)
        choices.append(GGUFChoice(key=key, files=grouped_files, primary=grouped_files[0]))

    return sorted(choices, key=lambda choice: choice.primary.path.lower())


def select_choice(
    choices: list[GGUFChoice],
    explicit_pattern: str | None,
    default_pattern: str | None,
) -> tuple[GGUFChoice, str | None]:
    # An explicit request always wins. A configured default is only a disambiguator:
    # it must not reject a repository that contains exactly one usable model.
    if explicit_pattern:
        pattern = explicit_pattern
    elif len(choices) == 1:
        return choices[0], None
    else:
        pattern = default_pattern or None
    candidates = choices
    if pattern:
        candidates = [
            choice
            for choice in choices
            if any(
                fnmatch.fnmatch(file.path, pattern) or fnmatch.fnmatch(PurePosixPath(file.path).name, pattern)
                for file in choice.files
            )
        ]
        if not candidates:
            raise AmbiguousSelection(
                f"Pattern {pattern!r} did not match a usable GGUF model.",
                [choice.display for choice in choices],
            )

    if len(candidates) != 1:
        message = (
            "GGUF selection is ambiguous; choose one quantization with --pattern."
            if candidates
            else "No GGUF selection is available."
        )
        raise AmbiguousSelection(message, [choice.display for choice in candidates or choices])
    return candidates[0], pattern
