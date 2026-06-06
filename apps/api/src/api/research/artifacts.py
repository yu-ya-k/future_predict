from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save_json(self, run_id: UUID, relative_path: str, payload: Any) -> tuple[str, str]:
        path = self.root / str(run_id) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        path.write_bytes(encoded)
        return str(path), hashlib.sha256(encoded).hexdigest()

    def save_text(self, run_id: UUID, relative_path: str, text: str) -> tuple[str, str]:
        path = self.root / str(run_id) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = text.encode("utf-8")
        path.write_bytes(encoded)
        return str(path), hashlib.sha256(encoded).hexdigest()

    def delete_run(self, run_id: UUID) -> None:
        shutil.rmtree(self.root / str(run_id), ignore_errors=True)
