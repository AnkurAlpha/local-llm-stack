from __future__ import annotations

import re
from dataclasses import dataclass

from huggingface_hub import HfApi, get_hf_file_metadata, hf_hub_url
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

from .errors import InvalidRepository, RepositoryAccessError
from .models import DownloadTarget, RemoteFile

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    repo_id: str
    revision: str
    files: tuple[RemoteFile, ...]


class HuggingFaceRepository:
    def __init__(self, token: str | None = None, api: HfApi | None = None) -> None:
        self.token = token.strip() if token and token.strip() else None
        self.api = api or HfApi(token=self.token)

    @staticmethod
    def validate_repo_id(repo_id: str) -> str:
        repo_id = repo_id.strip()
        if not REPO_RE.fullmatch(repo_id):
            raise InvalidRepository(
                "Repository ID must be exactly OWNER/REPO using safe Hugging Face characters."
            )
        return repo_id

    def inspect(self, repo_id: str, revision: str | None = None) -> RepositorySnapshot:
        repo_id = self.validate_repo_id(repo_id)
        try:
            info = self.api.model_info(
                repo_id=repo_id,
                revision=revision,
                files_metadata=True,
                token=self.token,
            )
        except GatedRepoError as exc:
            suffix = (
                " Set HF_TOKEN after accepting the repository terms."
                if not self.token
                else " Verify token access."
            )
            raise RepositoryAccessError(f"Repository is gated.{suffix}") from exc
        except RepositoryNotFoundError as exc:
            raise RepositoryAccessError(
                f"Hugging Face repository not found or not accessible: {repo_id}"
            ) from exc
        except HfHubHTTPError as exc:
            raise RepositoryAccessError(
                f"Hugging Face metadata request failed: {exc.__class__.__name__}"
            ) from exc

        remote_files: list[RemoteFile] = []
        for sibling in info.siblings or []:
            lfs = getattr(sibling, "lfs", None)
            size = getattr(sibling, "size", None) or getattr(lfs, "size", None)
            sha256 = getattr(lfs, "sha256", None)
            remote_files.append(
                RemoteFile(
                    path=str(sibling.rfilename),
                    size=int(size) if size is not None else None,
                    sha256=str(sha256) if sha256 else None,
                )
            )
        return RepositorySnapshot(repo_id=repo_id, revision=str(info.sha), files=tuple(remote_files))

    def resolve(self, snapshot: RepositorySnapshot, file: RemoteFile) -> DownloadTarget:
        url = hf_hub_url(snapshot.repo_id, file.path, revision=snapshot.revision)
        try:
            metadata = get_hf_file_metadata(url, token=self.token)
        except HfHubHTTPError as exc:
            raise RepositoryAccessError(f"Could not resolve download metadata for {file.path}") from exc
        return DownloadTarget(
            url=str(metadata.location or url),
            size=int(metadata.size) if metadata.size is not None else file.size,
            etag=str(metadata.etag) if metadata.etag else None,
            sha256=file.sha256,
        )
