from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import UUID


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_relative_path(relative_path: str) -> Path:
    posix_path = PurePosixPath(relative_path)
    windows_path = PureWindowsPath(relative_path)
    if (
        not relative_path
        or "\x00" in relative_path
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part == ".." for part in posix_path.parts)
        or any(part == ".." for part in windows_path.parts)
    ):
        raise ValueError("Artifact path must be a safe relative path.")

    path = Path(relative_path)
    if not path.parts or path == Path("."):
        raise ValueError("Artifact path must name a file.")
    return path


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save_json(self, run_id: UUID, relative_path: str, payload: Any) -> tuple[str, str]:
        path = self._prepare_artifact_path(run_id, relative_path)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        self._atomic_write(path, encoded)
        return str(path), hashlib.sha256(encoded).hexdigest()

    def save_text(self, run_id: UUID, relative_path: str, text: str) -> tuple[str, str]:
        path = self._prepare_artifact_path(run_id, relative_path)
        encoded = text.encode("utf-8")
        self._atomic_write(path, encoded)
        return str(path), hashlib.sha256(encoded).hexdigest()

    def delete_run(self, run_id: UUID) -> None:
        try:
            shutil.rmtree(self.root / str(run_id))
        except FileNotFoundError:
            return

    def _prepare_artifact_path(self, run_id: UUID, relative_path: str) -> Path:
        relative = _safe_relative_path(relative_path)
        run_root = self.root / str(run_id)
        run_root.mkdir(parents=True, exist_ok=True)
        run_root_resolved = run_root.resolve(strict=True)
        parent = self._ensure_parent_directory(run_root_resolved, relative.parent)
        path = parent / relative.name
        if path.exists() or path.is_symlink():
            try:
                resolved_path = path.resolve(strict=True)
            except FileNotFoundError as error:
                raise ValueError("Artifact path escapes the run root.") from error
            if not _is_relative_to(resolved_path, run_root_resolved):
                raise ValueError("Artifact path escapes the run root.")
        elif not _is_relative_to(path.resolve(strict=False), run_root_resolved):
            raise ValueError("Artifact path escapes the run root.")
        return path

    def _ensure_parent_directory(self, run_root: Path, relative_parent: Path) -> Path:
        current = run_root
        for part in relative_parent.parts:
            current = current / part
            if current.exists() or current.is_symlink():
                try:
                    resolved = current.resolve(strict=True)
                except FileNotFoundError as error:
                    raise ValueError("Artifact path escapes the run root.") from error
                if not resolved.is_dir() or not _is_relative_to(resolved, run_root):
                    raise ValueError("Artifact path escapes the run root.")
                current = resolved
                continue
            current.mkdir()
            resolved = current.resolve(strict=True)
            if not _is_relative_to(resolved, run_root):
                raise ValueError("Artifact path escapes the run root.")
            current = resolved
        return current

    def _atomic_write(self, path: Path, encoded: bytes) -> None:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(encoded)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
