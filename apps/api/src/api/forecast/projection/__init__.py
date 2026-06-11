from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from api.forecast.projection import phase_c_v1

ENGINE_VERSION = phase_c_v1.ENGINE_VERSION


@dataclass(frozen=True)
class ProjectionEngine:
    engine_version: str
    random_seed: int
    compute: Callable[..., dict[str, list[dict[str, Any]]]]
    engine_code_hash: Callable[[], str]
    snapshot_hash: Callable[[dict[str, Any]], str]
    canonical_json_bytes: Callable[[dict[str, Any]], bytes]


def get_engine(engine_version: str | None = None) -> ProjectionEngine:
    version = engine_version or phase_c_v1.ENGINE_VERSION
    if version != phase_c_v1.ENGINE_VERSION:
        raise ValueError(f"Unknown forecast projection engine: {version}")
    return ProjectionEngine(
        engine_version=phase_c_v1.ENGINE_VERSION,
        random_seed=phase_c_v1.RANDOM_SEED,
        compute=phase_c_v1.compute_phase_c_projection,
        engine_code_hash=phase_c_v1.engine_code_hash,
        snapshot_hash=phase_c_v1.snapshot_hash,
        canonical_json_bytes=phase_c_v1.canonical_json_bytes,
    )


def canonical_json_bytes(payload: dict[str, Any], *, engine_version: str | None = None) -> bytes:
    return get_engine(engine_version).canonical_json_bytes(payload)


__all__ = [
    "ENGINE_VERSION",
    "ProjectionEngine",
    "canonical_json_bytes",
    "get_engine",
]
