from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RemoteFile:
    path: str
    size: int | None = None
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class GGUFChoice:
    key: str
    files: tuple[RemoteFile, ...]
    primary: RemoteFile

    @property
    def total_size(self) -> int | None:
        if any(item.size is None for item in self.files):
            return None
        return sum(item.size or 0 for item in self.files)

    @property
    def display(self) -> str:
        size = "unknown size" if self.total_size is None else human_size(self.total_size)
        if len(self.files) == 1:
            return f"{self.primary.path} ({size})"
        return f"{self.primary.path} (+{len(self.files) - 1} shards, {size} total)"


@dataclass(frozen=True, slots=True)
class ModelFile:
    path: str
    size: int
    sha256: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ModelFile:
        return cls(path=str(value["path"]), size=int(value["size"]), sha256=value.get("sha256"))


@dataclass(frozen=True, slots=True)
class ModelRecord:
    model_id: str
    repo_id: str
    revision: str
    primary_file: str
    files: tuple[ModelFile, ...]
    total_size: int
    downloaded_at: str
    completed: bool
    pattern: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ModelRecord:
        return cls(
            model_id=str(value["model_id"]),
            repo_id=str(value["repo_id"]),
            revision=str(value["revision"]),
            primary_file=str(value["primary_file"]),
            files=tuple(ModelFile.from_dict(item) for item in value.get("files", [])),
            total_size=int(value.get("total_size", 0)),
            downloaded_at=str(value.get("downloaded_at", "")),
            completed=bool(value.get("completed", False)),
            pattern=value.get("pattern"),
        )


@dataclass(frozen=True, slots=True)
class DownloadTarget:
    url: str
    size: int | None
    etag: str | None
    sha256: str | None


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"
