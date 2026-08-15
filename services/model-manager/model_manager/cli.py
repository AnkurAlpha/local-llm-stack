from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from .downloader import Downloader, has_gguf_magic, safe_join
from .errors import AmbiguousSelection, ModelManagerError
from .huggingface import HuggingFaceRepository
from .models import ModelFile, ModelRecord, human_size
from .registry import Registry
from .selection import group_gguf_files, select_choice
from .state import CurrentModelState


def roots() -> tuple[Path, Path]:
    return Path(os.getenv("MODELS_ROOT", "/models")), Path(os.getenv("STATE_ROOT", "/state"))


def services() -> tuple[Registry, CurrentModelState]:
    models_root, state_root = roots()
    return Registry(state_root), CurrentModelState(state_root, models_root)


def manager() -> HuggingFaceRepository:
    return HuggingFaceRepository(token=os.getenv("HF_TOKEN"))


def model_id(repo_id: str, primary: str) -> str:
    raw = f"{repo_id.replace('/', '--')}--{Path(primary).name}"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")


def download_model(repo_id: str, pattern: str | None, revision: str | None = None) -> ModelRecord:
    models_root, _ = roots()
    registry, _ = services()
    hf = manager()
    snapshot = hf.inspect(repo_id, revision=revision)
    choices = group_gguf_files(list(snapshot.files))
    choice, pattern_used = select_choice(
        choices,
        explicit_pattern=pattern,
        default_pattern=os.getenv("DEFAULT_GGUF_PATTERN") or None,
    )
    print(f"Selected: {choice.display}")

    downloader = Downloader(
        connections=int(os.getenv("ARIA2_CONNECTIONS", "8")),
        retries=int(os.getenv("ARIA2_RETRIES", "10")),
        timeout=int(os.getenv("ARIA2_TIMEOUT", "60")),
        min_split_size=os.getenv("ARIA2_MIN_SPLIT_SIZE", "16M"),
        token=os.getenv("HF_TOKEN"),
    )
    destination_root = safe_join(models_root, snapshot.repo_id)
    outstanding = 0
    for item in choice.files:
        if item.size is None:
            continue
        destination = safe_join(destination_root, item.path)
        if destination.is_file() and destination.stat().st_size == item.size:
            continue
        partial = destination.with_name(destination.name + ".partial")
        partial_size = min(partial.stat().st_size, item.size) if partial.is_file() else 0
        outstanding += item.size - partial_size
    downloader.ensure_space(models_root, outstanding)

    installed: list[ModelFile] = []
    for remote in choice.files:
        print(f"Resolving: {remote.path}")
        target = hf.resolve(snapshot, remote)
        path = downloader.download(snapshot, remote, target, destination_root)
        relative = path.relative_to(models_root).as_posix()
        installed.append(ModelFile(path=relative, size=path.stat().st_size, sha256=target.sha256))

    primary_relative = safe_join(destination_root, choice.primary.path).relative_to(models_root).as_posix()
    record = ModelRecord(
        model_id=model_id(snapshot.repo_id, primary_relative),
        repo_id=snapshot.repo_id,
        revision=snapshot.revision,
        primary_file=primary_relative,
        files=tuple(installed),
        total_size=sum(item.size for item in installed),
        downloaded_at=datetime.now(UTC).isoformat(),
        completed=True,
        pattern=pattern_used,
    )
    registry.upsert(record)
    print(f"Installed: {record.model_id} ({human_size(record.total_size)})")
    return record


def command_download(args: argparse.Namespace) -> int:
    download_model(args.repo_id, args.pattern, args.revision)
    return 0


def command_remote(args: argparse.Namespace) -> int:
    snapshot = manager().inspect(args.repo_id, revision=args.revision)
    print(f"Repository: {snapshot.repo_id}")
    print(f"Revision:   {snapshot.revision}")
    for choice in group_gguf_files(list(snapshot.files)):
        print(f"  {choice.display}")
    return 0


def command_list(_: argparse.Namespace) -> int:
    models_root, _ = roots()
    registry, state = services()
    current = state.read() or {}
    records = registry.list()
    if not records:
        print("No completed models installed.")
        return 0
    for record in records:
        marker = "*" if record.model_id == current.get("model_id") else " "
        healthy = all(
            safe_join(models_root, item.path).is_file()
            and safe_join(models_root, item.path).stat().st_size == item.size
            for item in record.files
        )
        status = "OK" if healthy else "BROKEN"
        identity = f"{record.repo_id}@{record.revision[:12]}"
        print(f"{marker} {record.model_id}  {human_size(record.total_size)}  {status}  {identity}")
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    models_root, _ = roots()
    registry, _ = services()
    record = registry.resolve(args.model)
    payload = record.to_dict()
    payload["file_status"] = [
        {
            "path": item.path,
            "exists": safe_join(models_root, item.path).is_file(),
            "size_ok": safe_join(models_root, item.path).is_file()
            and safe_join(models_root, item.path).stat().st_size == item.size,
            "gguf_magic_ok": has_gguf_magic(safe_join(models_root, item.path)),
        }
        for item in record.files
    ]
    print(json.dumps(payload, indent=2))
    return 0


def command_use(args: argparse.Namespace) -> int:
    registry, state = services()
    record = registry.resolve(args.model)
    state.select(record)
    print(f"Selected: {record.model_id}")
    print(f"Primary:  {record.primary_file}")
    return 0


def command_current(_: argparse.Namespace) -> int:
    _, state = services()
    current = state.read()
    if current is None:
        print("No model selected.")
        return 0
    print(json.dumps(current, indent=2))
    return 0


def command_remove(args: argparse.Namespace) -> int:
    if not args.yes:
        raise ModelManagerError("Removal requires --yes (llmctl asks for confirmation before adding it).")
    models_root, _ = roots()
    registry, state = services()
    record = registry.resolve(args.model)
    current = state.read() or {}
    if current.get("model_id") == record.model_id:
        intact = all(
            safe_join(models_root, item.path).is_file()
            and safe_join(models_root, item.path).stat().st_size == item.size
            and has_gguf_magic(safe_join(models_root, item.path))
            for item in record.files
        )
        if intact:
            raise ModelManagerError("Refusing to remove the selected model. Select another model first.")
        state.clear()
        print("Selected model was already missing or invalid; cleared the stale selection.")
    for item in record.files:
        path = safe_join(models_root, item.path)
        path.unlink(missing_ok=True)
        parent = path.parent
        while parent != models_root and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    registry.remove(record.model_id)
    print(f"Removed: {record.model_id}")
    return 0


def command_health(_: argparse.Namespace) -> int:
    models_root, state_root = roots()
    models_root.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)
    Registry(state_root).list()
    if not os.access(models_root, os.W_OK) or not os.access(state_root, os.W_OK):
        raise ModelManagerError("Model/state mounts are not writable by the configured container UID/GID.")
    print("ok")
    return 0


def command_daemon(_: argparse.Namespace) -> int:
    command_health(argparse.Namespace())
    registry, _ = services()
    bootstrap_repo = os.getenv("BOOTSTRAP_MODEL_REPO", "").strip()
    if bootstrap_repo and not registry.list():
        print(f"Explicit bootstrap requested for {bootstrap_repo}")
        try:
            download_model(bootstrap_repo, os.getenv("BOOTSTRAP_MODEL_PATTERN") or None)
        except (ModelManagerError, ValueError) as exc:
            print(f"Bootstrap failed: {exc}", file=sys.stderr)

    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda _signum, _frame: stop.set())
    print("model-manager daemon ready")
    stop.wait()
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="model-manager")
    sub = root.add_subparsers(dest="command", required=True)

    download = sub.add_parser("download")
    download.add_argument("repo_id")
    download.add_argument("--pattern")
    download.add_argument("--revision")
    download.set_defaults(handler=command_download)

    remote = sub.add_parser("inspect-repo")
    remote.add_argument("repo_id")
    remote.add_argument("--revision")
    remote.set_defaults(handler=command_remote)

    list_parser = sub.add_parser("list")
    list_parser.set_defaults(handler=command_list)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("model")
    inspect.set_defaults(handler=command_inspect)

    use = sub.add_parser("use")
    use.add_argument("model")
    use.set_defaults(handler=command_use)

    current = sub.add_parser("current")
    current.set_defaults(handler=command_current)

    remove = sub.add_parser("remove")
    remove.add_argument("model")
    remove.add_argument("--yes", action="store_true")
    remove.set_defaults(handler=command_remove)

    health = sub.add_parser("health")
    health.set_defaults(handler=command_health)

    daemon = sub.add_parser("daemon")
    daemon.set_defaults(handler=command_daemon)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except AmbiguousSelection as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if exc.choices:
            print("Available choices:", file=sys.stderr)
            for choice in exc.choices:
                print(f"  - {choice}", file=sys.stderr)
            print('Retry with: ./llmctl download OWNER/REPO --pattern "*QUANT*.gguf"', file=sys.stderr)
        return 2
    except (ModelManagerError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Download interrupted; resumable partial files were preserved.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
