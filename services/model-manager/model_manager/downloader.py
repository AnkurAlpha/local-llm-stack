from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from huggingface_hub import hf_hub_download

from .errors import DownloadError, IntegrityError, UnsafePath
from .huggingface import RepositorySnapshot
from .models import DownloadTarget, RemoteFile


def safe_join(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise UnsafePath(f"Unsafe repository filename: {relative!r}")
    candidate = root.joinpath(*pure.parts).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise UnsafePath(f"Repository filename escapes model directory: {relative!r}")
    return candidate


def has_gguf_magic(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"GGUF"
    except OSError:
        return False


class Downloader:
    def __init__(
        self,
        connections: int = 8,
        retries: int = 10,
        timeout: int = 60,
        min_split_size: str = "16M",
        token: str | None = None,
    ) -> None:
        if not 1 <= connections <= 16:
            raise DownloadError("ARIA2_CONNECTIONS must be between 1 and 16.")
        self.connections = connections
        self.retries = max(1, retries)
        self.timeout = max(10, timeout)
        self.min_split_size = min_split_size
        self.token = token.strip() if token and token.strip() else None

    @staticmethod
    def validate_complete(
        path: Path,
        expected_size: int | None,
        expected_sha256: str | None = None,
    ) -> None:
        if not path.is_file():
            raise IntegrityError(f"Downloaded file is missing: {path.name}")
        actual_size = path.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            message = (
                f"Size mismatch for {path.name}: expected {expected_size}, received {actual_size}. "
                "Partial file was preserved."
            )
            raise IntegrityError(message)
        if actual_size < 8 or not has_gguf_magic(path):
            raise IntegrityError(
                f"Invalid GGUF header in {path.name}; file was not promoted to a completed model."
            )
        if expected_sha256 and file_sha256(path).lower() != expected_sha256.lower():
            raise IntegrityError(
                f"SHA-256 mismatch for {path.name}; file was not promoted to a completed model."
            )

    @staticmethod
    def ensure_space(directory: Path, required: int) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(directory).free
        reserve = max(256 * 1024 * 1024, int(required * 0.02))
        if free < required + reserve:
            message = (
                f"Insufficient disk space: need about {required + reserve} bytes, "
                f"only {free} bytes available."
            )
            raise DownloadError(message)

    def _aria2(self, target: DownloadTarget, partial: Path) -> None:
        aria2 = shutil.which("aria2c")
        if aria2 is None:
            raise DownloadError("aria2c is unavailable")
        parsed = urlparse(target.url)
        if parsed.scheme not in {"http", "https"}:
            raise DownloadError("resolved URL is not HTTP(S)")
        # Avoid putting a bearer token in a process argument. Signed CDN URLs need no header.
        if self.token and parsed.hostname and parsed.hostname.endswith("huggingface.co"):
            raise DownloadError("authenticated direct URL uses the huggingface.co host")

        command = [
            aria2,
            "--continue=true",
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            "--file-allocation=none",
            "--check-integrity=true",
            f"--max-connection-per-server={self.connections}",
            f"--split={self.connections}",
            f"--min-split-size={self.min_split_size}",
            f"--max-tries={self.retries}",
            "--retry-wait=5",
            f"--timeout={self.timeout}",
            "--connect-timeout=30",
            "--console-log-level=warn",
            "--summary-interval=10",
            "--download-result=hide",
            f"--dir={partial.parent}",
            f"--out={partial.name}",
        ]
        if target.sha256 and len(target.sha256) == 64:
            command.append(f"--checksum=sha-256={target.sha256}")
        command.append(target.url)

        process = subprocess.Popen(command, start_new_session=False)
        try:
            code = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        if code != 0:
            raise DownloadError(f"aria2c exited with code {code}")

    def _huggingface_fallback(
        self,
        snapshot: RepositorySnapshot,
        remote: RemoteFile,
        partial: Path,
    ) -> None:
        try:
            # local_dir keeps resumable Hub staging beside the destination instead
            # of caching a second multi-gigabyte copy under /state/HF_HOME.
            staging = partial.parent / ".hf-downloads"
            cached = Path(
                hf_hub_download(
                    repo_id=snapshot.repo_id,
                    filename=remote.path,
                    revision=snapshot.revision,
                    token=self.token,
                    local_dir=staging,
                )
            )
            os.replace(cached, partial)
        except Exception as exc:
            raise DownloadError(
                f"huggingface_hub fallback failed for {remote.path}: {exc.__class__.__name__}"
            ) from exc

    def download(
        self,
        snapshot: RepositorySnapshot,
        remote: RemoteFile,
        target: DownloadTarget,
        destination_root: Path,
    ) -> Path:
        destination = safe_join(destination_root, remote.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self.validate_complete(destination, target.size)
            return destination

        partial = destination.with_name(destination.name + ".partial")
        used_fallback = False
        try:
            self._aria2(target, partial)
        except DownloadError as aria_error:
            used_fallback = True
            print(
                f"aria2 unavailable/inappropriate for {remote.path}; "
                f"using huggingface_hub resume fallback ({aria_error})."
            )
            self._huggingface_fallback(snapshot, remote, partial)

        # aria2 verifies a supplied LFS checksum itself. Verify explicitly when the
        # huggingface_hub fallback supplied the bytes instead.
        self.validate_complete(partial, target.size, target.sha256 if used_fallback else None)
        os.replace(partial, destination)
        control_file = partial.with_name(partial.name + ".aria2")
        control_file.unlink(missing_ok=True)
        return destination


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
