from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import pytest

import api.forecast.probability.phase_a_v1 as phase_a_v1
from api.forecast.artifacts import ForecastArtifactStore


def _snapshot() -> dict[str, object]:
    return {
        "epsilon_floor": 0.01,
        "kappa": 2.0,
        "clamp": 0.5,
        "outcomes": [
            {"outcome_id": "yes"},
            {"outcome_id": "no"},
        ],
        "claims": [
            {
                "claim_id": "c1",
                "evidence_strength": 1.0,
                "reliability_score": 1.0,
                "cluster_id": "cluster-a",
                "independence_group": "group-a",
            },
            {
                "claim_id": "c2",
                "evidence_strength": 1.0,
                "reliability_score": 1.0,
                "cluster_id": "cluster-b",
                "independence_group": "group-b",
            },
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": "yes",
                "direction": 1,
                "relevance_weight": 1.0,
            },
            {
                "claim_id": "c2",
                "target_kind": "outcome",
                "target_id": "no",
                "direction": -1,
                "relevance_weight": 1.0,
            },
        ],
    }


def test_compute_phase_a_estimates_uses_snapshot_parameters() -> None:
    estimates = phase_a_v1.compute_phase_a_estimates(snapshot=_snapshot())

    components = estimates[0]["components"]
    assert components["epsilon_floor"] == 0.01
    assert components["kappa"] == 2.0
    assert components["clamp"] == 0.5
    assert components["clamped"] is True
    assert estimates[0]["uncertainty_range"]["lo80"] >= 0.01


def test_compute_phase_a_estimates_allows_explicit_parameter_override() -> None:
    estimates = phase_a_v1.compute_phase_a_estimates(
        snapshot=_snapshot(),
        epsilon_floor=0.0,
        kappa=1.0,
        clamp=3.0,
    )

    components = estimates[0]["components"]
    assert components["epsilon_floor"] == 0.0
    assert components["kappa"] == 1.0
    assert components["clamp"] == 3.0
    assert components["clamped"] is False


@pytest.mark.parametrize(
    "covered_function",
    [
        "canonical_json_bytes",
        "snapshot_hash",
        "compute_phase_a_estimates",
        "multiclass_brier",
        "log_score",
        "_epsilon_softmax",
        "_stable",
    ],
)
def test_engine_code_hash_covers_engine_function_sources(
    monkeypatch: pytest.MonkeyPatch,
    covered_function: str,
) -> None:
    original_hash = phase_a_v1.engine_code_hash()
    original_getsource = phase_a_v1.inspect.getsource
    target = getattr(phase_a_v1, covered_function)

    def fake_getsource(function: Callable[..., object]) -> str:
        source = original_getsource(function)
        if function is target:
            return f"{source}\n# test mutation"
        return source

    monkeypatch.setattr(phase_a_v1.inspect, "getsource", fake_getsource)

    assert phase_a_v1.engine_code_hash() != original_hash


def test_engine_code_hash_covers_engine_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_hash = phase_a_v1.engine_code_hash()

    monkeypatch.setattr(phase_a_v1, "DEFAULT_CLAMP", 9.0)

    assert phase_a_v1.engine_code_hash() != original_hash


def test_private_artifact_permissions_are_explicit(tmp_path: Path) -> None:
    root = tmp_path / "forecast-artifacts"
    forecast_id = uuid4()
    store = ForecastArtifactStore(root)

    path, digest, data = store.save_bytes(
        forecast_id,
        "private",
        "nested/snapshot.json",
        b'{"ok":true}',
    )

    assert digest
    assert data == b'{"ok":true}'
    target = Path(path)
    checked_paths = [
        root,
        root / str(forecast_id),
        root / str(forecast_id) / "private",
        root / str(forecast_id) / "private" / "nested",
    ]
    for directory in checked_paths:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_artifact_store_fsyncs_parent_directory_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    directory_fsyncs = 0

    def recording_fsync(fd: int) -> None:
        nonlocal directory_fsyncs
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_fsyncs += 1
        real_fsync(fd)

    monkeypatch.setattr("api.forecast.artifacts.os.fsync", recording_fsync)
    store = ForecastArtifactStore(tmp_path / "forecast-artifacts")

    store.save_bytes(uuid4(), "public", "snapshot.json", b"{}")

    assert directory_fsyncs == 1
