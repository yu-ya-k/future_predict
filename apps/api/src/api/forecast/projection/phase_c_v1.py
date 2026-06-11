from __future__ import annotations

import hashlib
import inspect
import json
import math
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from api.forecast.errors import ForecastInvalidInput

ENGINE_VERSION = "phase_c_v1"
RANDOM_SEED = 0
CONSTANTS_VERSION = "phase_c_v1_constants"
EPSILON_FLOOR = 1e-9
RESIDUAL_FLOOR = 0.05
K_EVIDENCE = 0.9
K_COUNTER = 0.45
K_SIGNAL = 0.35
RESIDUAL_PENALTY = 1.0


def _parse_finite_float(
    value: Any,
    *,
    field: str,
    lo: float | None = None,
    hi: float | None = None,
) -> float:
    """Parse *value* as float, reject non-finite results, and optionally validate range."""
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ForecastInvalidInput(
            "invalid_numeric_field",
            f"Field '{field}' could not be parsed as a number: {value!r}",
            {"field": field, "value": repr(value)},
        ) from exc
    if not math.isfinite(result):
        raise ForecastInvalidInput(
            "non_finite_numeric_field",
            f"Field '{field}' must be a finite number, got {result!r}",
            {"field": field, "value": repr(value)},
        )
    if lo is not None and result < lo:
        raise ForecastInvalidInput(
            "numeric_field_out_of_range",
            f"Field '{field}' must be >= {lo}, got {result!r}",
            {"field": field, "value": repr(value), "lo": lo},
        )
    if hi is not None and result > hi:
        raise ForecastInvalidInput(
            "numeric_field_out_of_range",
            f"Field '{field}' must be <= {hi}, got {result!r}",
            {"field": field, "value": repr(value), "hi": hi},
        )
    return result


def _parse_int(value: Any, *, field: str) -> int:
    """Parse *value* as an int, raising ForecastInvalidInput on non-integer input."""
    if isinstance(value, bool):
        raise ForecastInvalidInput(
            "invalid_numeric_field",
            f"Field '{field}' could not be parsed as an integer: {value!r}",
            {"field": field, "value": repr(value)},
        )
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ForecastInvalidInput(
            "invalid_numeric_field",
            f"Field '{field}' could not be parsed as an integer: {value!r}",
            {"field": field, "value": repr(value)},
        ) from exc


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        _stable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def snapshot_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def engine_code_hash() -> str:
    return hashlib.sha256(canonical_json_bytes(_engine_hash_payload())).hexdigest()


def compute_phase_c_projection(*, snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    input_hash = snapshot_hash(snapshot)
    scenarios = _scenario_probabilities(snapshot)
    metric_points = _metric_points(snapshot, scenarios)
    composites = _composites(snapshot, scenarios, metric_points)
    sensitivities = _sensitivities(snapshot, scenarios, composites, input_hash)
    return {
        "scenarios": scenarios,
        "metric_points": metric_points,
        "composites": composites,
        "sensitivities": sensitivities,
    }


def _scenario_probabilities(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    claims = snapshot.get("claims", [])
    support = math.fsum(
        _parse_finite_float(
            claim.get("evidence_strength", 0.0), field="evidence_strength", lo=0.0, hi=1.0
        )
        * _parse_finite_float(
            claim.get("reliability_score", 0.0), field="reliability_score", lo=0.0, hi=1.0
        )
        for claim in claims
        if int(claim.get("polarity", 1)) >= 0
    )
    counter = math.fsum(
        _parse_finite_float(
            claim.get("evidence_strength", 0.0), field="evidence_strength", lo=0.0, hi=1.0
        )
        * _parse_finite_float(
            claim.get("reliability_score", 0.0), field="reliability_score", lo=0.0, hi=1.0
        )
        for claim in claims
        if int(claim.get("polarity", 1)) < 0
    )
    signal = math.fsum(
        1.0
        for pack in snapshot.get("packs", [])
        if pack.get("pack_role") == "signals" and pack.get("report_artifact_hash")
    )
    candidates = [
        {
            "key": "accelerated",
            "label": "Accelerated transition",
            "coverage_role": "core",
            "driver_state_prior": 0.34,
            "support_multiplier": 1.0,
            "counter_multiplier": 0.4,
            "signal_multiplier": 1.0,
            "growth": 1.35,
        },
        {
            "key": "baseline",
            "label": "Baseline transition",
            "coverage_role": "core",
            "driver_state_prior": 0.46,
            "support_multiplier": 0.55,
            "counter_multiplier": 0.55,
            "signal_multiplier": 0.4,
            "growth": 1.0,
        },
        {
            "key": "residual",
            "label": "Residual and discontinuity",
            "coverage_role": "residual",
            "driver_state_prior": 0.20,
            "support_multiplier": 0.1,
            "counter_multiplier": 0.9,
            "signal_multiplier": 0.1,
            "growth": 0.72,
        },
    ]
    logits: list[float] = []
    for candidate in candidates:
        residual_penalty = RESIDUAL_PENALTY if candidate["key"] == "residual" else 0.0
        logits.append(
            math.log(max(float(candidate["driver_state_prior"]), EPSILON_FLOOR))
            + K_EVIDENCE * support * float(candidate["support_multiplier"])
            - K_COUNTER * counter * float(candidate["counter_multiplier"])
            + K_SIGNAL * signal * float(candidate["signal_multiplier"])
            - residual_penalty
        )
    probabilities = _softmax(logits)
    residual_index = 2
    if probabilities[residual_index] < RESIDUAL_FLOOR:
        scale = (1.0 - RESIDUAL_FLOOR) / (1.0 - probabilities[residual_index])
        probabilities = [
            RESIDUAL_FLOOR if index == residual_index else probability * scale
            for index, probability in enumerate(probabilities)
        ]
    total = math.fsum(probabilities)
    probabilities = [probability / total for probability in probabilities]
    forecast_id = str(snapshot["forecast"]["forecast_id"])
    output: list[dict[str, Any]] = []
    for candidate, logit, probability in zip(candidates, logits, probabilities, strict=True):
        scenario_id = str(
            uuid5(
                NAMESPACE_URL,
                f"phase-c-scenario:{forecast_id}:{candidate['key']}:{input_signature(snapshot)}",
            )
        )
        output.append(
            {
                "projection_scenario_id": scenario_id,
                "label": candidate["label"],
                "description": f"{candidate['label']} for {snapshot['forecast']['question']}",
                "coverage_role": candidate["coverage_role"],
                "residual_flag": candidate["key"] == "residual",
                "probability": probability,
                "probability_logit": logit,
                "driver_vector": {
                    "support": support,
                    "counter": counter,
                    "signal": signal,
                    "growth": candidate["growth"],
                    "constants_version": CONSTANTS_VERSION,
                },
                "narrative": f"{candidate['label']} projection path.",
                "validity_status": "valid",
            }
        )
    return output


def _metric_points(
    snapshot: dict[str, Any],
    scenarios: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    forecast_id = str(snapshot["forecast"]["forecast_id"])
    rows: list[dict[str, Any]] = []
    for dimension in snapshot.get("dimensions", []):
        baseline = _parse_finite_float(
            dimension["baseline_value"], field="baseline_value", lo=0.0
        )
        baseline_year = _parse_int(dimension["baseline_year"], field="baseline_year")
        horizons = sorted(_parse_int(year, field="horizon_year") for year in dimension["horizons"])
        for scenario in scenarios:
            growth = _parse_finite_float(
                scenario["driver_vector"]["growth"], field="growth"
            )
            for horizon in horizons:
                years = max(1, horizon - baseline_year)
                factor = max(0.0, 1.0 + growth * years / 20.0)
                p50 = baseline * factor
                spread = max(0.05, 0.12 + 0.01 * min(years, 20))
                p10 = max(0.0, p50 * (1.0 - spread))
                p90 = max(p50, p50 * (1.0 + spread))
                mean = (p10 + p50 + p90) / 3.0
                rows.append(
                    {
                        "metric_point_id": str(
                            uuid5(
                                NAMESPACE_URL,
                                "phase-c-point:"
                                f"{forecast_id}:{scenario['projection_scenario_id']}:"
                                f"{dimension['dimension_id']}:{horizon}",
                            )
                        ),
                        "projection_scenario_id": scenario["projection_scenario_id"],
                        "dimension_id": dimension["dimension_id"],
                        "metric_id": dimension["metric_id"],
                        "horizon_year": horizon,
                        "p10": p10,
                        "p50": p50,
                        "p90": p90,
                        "mean": mean,
                        "distribution_family": "triangular_quantile_v1",
                        "distribution_params": {"p10": p10, "p50": p50, "p90": p90},
                        "baseline_transform": "linear_growth_from_baseline",
                    }
                )
    return rows


def _composites(
    snapshot: dict[str, Any],
    scenarios: list[dict[str, Any]],
    metric_points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    forecast_id = str(snapshot["forecast"]["forecast_id"])
    by_dimension_horizon: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for point in metric_points:
        by_dimension_horizon.setdefault(
            (str(point["dimension_id"]), int(point["horizon_year"])),
            [],
        ).append(point)
    probability_by_scenario = {
        str(scenario["projection_scenario_id"]): float(scenario["probability"])
        for scenario in scenarios
    }
    rows: list[dict[str, Any]] = []
    for (dimension_id, horizon), points in sorted(by_dimension_horizon.items()):
        components = [
            {
                "projection_scenario_id": point["projection_scenario_id"],
                "probability": probability_by_scenario[point["projection_scenario_id"]],
                "distribution_params": point["distribution_params"],
            }
            for point in sorted(points, key=lambda item: item["projection_scenario_id"])
        ]
        p10_terms: list[float] = []
        p50_terms: list[float] = []
        p90_terms: list[float] = []
        mean_terms: list[float] = []
        for component in components:
            probability = float(component["probability"])
            params = cast(dict[str, float], component["distribution_params"])
            p10_terms.append(probability * float(params["p10"]))
            p50_terms.append(probability * float(params["p50"]))
            p90_terms.append(probability * float(params["p90"]))
            mean_terms.append(
                probability
                * (float(params["p10"]) + float(params["p50"]) + float(params["p90"]))
                / 3.0
            )
        p10 = math.fsum(p10_terms)
        p50 = math.fsum(p50_terms)
        p90 = math.fsum(p90_terms)
        mean = math.fsum(mean_terms)
        metric_id = str(points[0]["metric_id"])
        rows.append(
            {
                "composite_id": str(
                    uuid5(
                        NAMESPACE_URL,
                        f"phase-c-composite:{forecast_id}:{dimension_id}:{horizon}",
                    )
                ),
                "dimension_id": dimension_id,
                "metric_id": metric_id,
                "horizon_year": horizon,
                "p10": p10,
                "p50": p50,
                "p90": p90,
                "mean": mean,
                "mixture_components": components,
            }
        )
    return rows


def _sensitivities(
    snapshot: dict[str, Any],
    scenarios: list[dict[str, Any]],
    composites: list[dict[str, Any]],
    input_hash: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    forecast_id = str(snapshot["forecast"]["forecast_id"])
    for index, scenario in enumerate(sorted(scenarios, key=lambda item: item["label"])):
        rows.append(
            {
                "sensitivity_id": str(
                    uuid5(
                        NAMESPACE_URL,
                        f"phase-c-sensitivity-prob:{forecast_id}:{scenario['projection_scenario_id']}",
                    )
                ),
                "sensitivity_kind": "scenario_probability",
                "target_ref": scenario["projection_scenario_id"],
                "baseline_snapshot_hash": input_hash,
                "perturbed_input": {"probability_shift": 0.01},
                "delta_p50": 0.0,
                "delta_p90": 0.0,
                "delta_probability": 0.01,
                "rank": index + 1,
            }
        )
    for index, composite in enumerate(sorted(composites, key=lambda item: item["composite_id"])):
        rows.append(
            {
                "sensitivity_id": str(
                    uuid5(
                        NAMESPACE_URL,
                        f"phase-c-sensitivity-driver:{forecast_id}:{composite['composite_id']}",
                    )
                ),
                "sensitivity_kind": "driver_one_way",
                "target_ref": composite["composite_id"],
                "baseline_snapshot_hash": input_hash,
                "perturbed_input": {"growth_multiplier": 1.1},
                "delta_p50": float(composite["p50"]) * 0.1,
                "delta_p90": float(composite["p90"]) * 0.1,
                "delta_probability": 0.0,
                "rank": len(scenarios) + index + 1,
            }
        )
    return rows


def input_signature(snapshot: dict[str, Any]) -> str:
    payload = {
        "forecast": snapshot.get("forecast", {}),
        "dimensions": snapshot.get("dimensions", []),
        "claims": [
            {
                "claim_id": claim.get("claim_id"),
                "polarity": claim.get("polarity"),
                "evidence_strength": claim.get("evidence_strength"),
                "reliability_score": claim.get("reliability_score"),
            }
            for claim in snapshot.get("claims", [])
        ],
        "packs": snapshot.get("packs", []),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:16]


def _softmax(logits: list[float]) -> list[float]:
    if not logits:
        raise ForecastInvalidInput(
            "softmax_empty_logits",
            "Cannot compute softmax over an empty logit list.",
            {},
        )
    max_logit = max(logits)
    exps = [math.exp(logit - max_logit) for logit in logits]
    total = math.fsum(exps)
    return [max(value / total, EPSILON_FLOOR) for value in exps]


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        typed_dict = cast(dict[Any, Any], value)
        items = sorted(typed_dict.items())
        return {str(key): _stable(item) for key, item in items}
    if isinstance(value, list):
        return [_stable(item) for item in cast(list[Any], value)]
    if isinstance(value, tuple):
        return [_stable(item) for item in cast(tuple[Any, ...], value)]
    return value


def _engine_hash_payload() -> dict[str, Any]:
    covered_functions = [
        canonical_json_bytes,
        snapshot_hash,
        compute_phase_c_projection,
        _scenario_probabilities,
        _metric_points,
        _composites,
        _sensitivities,
        input_signature,
        _softmax,
        _stable,
    ]
    return {
        "engine_version": ENGINE_VERSION,
        "constants_version": CONSTANTS_VERSION,
        "constants": {
            "epsilon_floor": EPSILON_FLOOR,
            "residual_floor": RESIDUAL_FLOOR,
            "k_evidence": K_EVIDENCE,
            "k_counter": K_COUNTER,
            "k_signal": K_SIGNAL,
            "residual_penalty": RESIDUAL_PENALTY,
            "random_seed": RANDOM_SEED,
        },
        "sources": {
            function.__name__: inspect.getsource(function)
            for function in covered_functions
        },
    }
