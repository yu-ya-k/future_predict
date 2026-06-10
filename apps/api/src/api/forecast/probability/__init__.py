from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from api.forecast.probability import phase_a_v1, phase_b_v1

ENGINE_VERSION = phase_a_v1.ENGINE_VERSION


@dataclass(frozen=True)
class ProbabilityEngine:
    engine_version: str
    scorer_version: str
    random_seed: int
    compute: Callable[..., list[dict[str, Any]]]
    engine_code_hash: Callable[[], str]
    snapshot_hash: Callable[[dict[str, Any]], str]
    canonical_json_bytes: Callable[[dict[str, Any]], bytes]
    multiclass_brier: Callable[..., float]
    log_score: Callable[..., float]


def get_engine(engine_version: str | None = None) -> ProbabilityEngine:
    version = engine_version or phase_a_v1.ENGINE_VERSION
    if version == phase_a_v1.ENGINE_VERSION:
        return ProbabilityEngine(
            engine_version=phase_a_v1.ENGINE_VERSION,
            scorer_version=phase_a_v1.SCORER_VERSION,
            random_seed=phase_a_v1.RANDOM_SEED,
            compute=phase_a_v1.compute_phase_a_estimates,
            engine_code_hash=phase_a_v1.engine_code_hash,
            snapshot_hash=phase_a_v1.snapshot_hash,
            canonical_json_bytes=phase_a_v1.canonical_json_bytes,
            multiclass_brier=phase_a_v1.multiclass_brier,
            log_score=phase_a_v1.log_score,
        )
    if version == phase_b_v1.ENGINE_VERSION:
        return ProbabilityEngine(
            engine_version=phase_b_v1.ENGINE_VERSION,
            scorer_version=phase_b_v1.SCORER_VERSION,
            random_seed=phase_b_v1.RANDOM_SEED,
            compute=phase_b_v1.compute_phase_b_estimates,
            engine_code_hash=phase_b_v1.engine_code_hash,
            snapshot_hash=phase_b_v1.snapshot_hash,
            canonical_json_bytes=phase_b_v1.canonical_json_bytes,
            multiclass_brier=phase_b_v1.multiclass_brier,
            log_score=phase_b_v1.log_score,
        )
    raise ValueError(f"Unknown forecast probability engine: {version}")


def compute(
    *,
    snapshot: dict[str, Any],
    engine_version: str | None = None,
) -> list[dict[str, Any]]:
    return get_engine(engine_version).compute(snapshot=snapshot)


def score(
    *,
    estimates: list[dict[str, Any]],
    actual_outcome_id: str,
    engine_version: str,
) -> tuple[float, float, str]:
    engine = get_engine(engine_version)
    return (
        engine.multiclass_brier(estimates, actual_outcome_id=actual_outcome_id),
        engine.log_score(estimates, actual_outcome_id=actual_outcome_id),
        engine.scorer_version,
    )


def engine_code_hash(engine_version: str | None = None) -> str:
    return get_engine(engine_version).engine_code_hash()


def snapshot_hash(payload: dict[str, Any], *, engine_version: str | None = None) -> str:
    return get_engine(engine_version).snapshot_hash(payload)


def canonical_json_bytes(payload: dict[str, Any], *, engine_version: str | None = None) -> bytes:
    return get_engine(engine_version).canonical_json_bytes(payload)


compute_phase_a_estimates = phase_a_v1.compute_phase_a_estimates

__all__ = [
    "ENGINE_VERSION",
    "ProbabilityEngine",
    "canonical_json_bytes",
    "compute",
    "compute_phase_a_estimates",
    "engine_code_hash",
    "get_engine",
    "score",
    "snapshot_hash",
]
