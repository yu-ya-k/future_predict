from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from uuid import UUID


class ForecastArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save_json(
        self,
        forecast_id: UUID,
        tool_profile: str,
        relative_path: str,
        payload: object,
    ) -> tuple[str, str, bytes]:
        data = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode(
            "utf-8",
        )
        return self.save_bytes(forecast_id, tool_profile, relative_path, data)

    def save_bytes(
        self,
        forecast_id: UUID,
        tool_profile: str,
        relative_path: str,
        data: bytes,
    ) -> tuple[str, str, bytes]:
        target = self._safe_target(forecast_id, tool_profile, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if tool_profile == "private":
            self._chmod_private_directories(target.parent)
        digest = hashlib.sha256(data).hexdigest()
        old_umask: int | None = None
        temp_name: str | None = None
        if tool_profile == "private":
            old_umask = os.umask(0o077)
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                temp_name = handle.name
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if tool_profile == "private":
                os.chmod(temp_name, 0o600)
            os.replace(temp_name, target)
            if tool_profile == "private":
                os.chmod(target, 0o600)
            self._fsync_directory(target.parent)
        finally:
            if old_umask is not None:
                os.umask(old_umask)
            if temp_name is not None and os.path.exists(temp_name):
                os.unlink(temp_name)
        return str(target), digest, data

    def _safe_target(self, forecast_id: UUID, tool_profile: str, relative_path: str) -> Path:
        if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
            raise ValueError("Unsafe artifact path.")
        root = (self.root / str(forecast_id) / tool_profile).resolve()
        target = (root / relative_path).resolve()
        if root != target and root not in target.parents:
            raise ValueError("Unsafe artifact path.")
        return target

    def _chmod_private_directories(self, leaf: Path) -> None:
        root = self.root.resolve()
        current = leaf.resolve()
        paths = [current]
        while current != root:
            if root not in current.parents:
                raise ValueError("Unsafe artifact path.")
            current = current.parent
            paths.append(current)
        for path in reversed(paths):
            os.chmod(path, 0o700)

    def _fsync_directory(self, path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
