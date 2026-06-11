"""Golden numeric unit tests for Phase A/B/C probability and projection engines.

These tests call internal functions directly — no HTTP stack, no DB.
They cover:
  - phase_b: multiclass_brier, log_score, _clamp_update, _epsilon_softmax,
    _percentile_range
  - phase_c: projection percentile/composite math (p10 <= p50 <= p90, mean,
    scenario probability)
  - Validation: NaN/inf/garbage numerics raise ForecastInvalidInput (domain
    error), empty logits and K*epsilon_floor >= 1 raise the same error.
"""
from __future__ import annotations

import math
from typing import Any, cast

import pytest

from api.forecast.errors import ForecastInvalidInput
from api.forecast.probability.phase_b_v1 import (
    _clamp_update,  # pyright: ignore[reportPrivateUsage]
    _epsilon_softmax,  # pyright: ignore[reportPrivateUsage]
    _percentile_range,  # pyright: ignore[reportPrivateUsage]
    log_score,
    multiclass_brier,
)
from api.forecast.projection.phase_c_v1 import (
    _softmax,  # pyright: ignore[reportPrivateUsage]
    compute_phase_c_projection,
)

# ---------------------------------------------------------------------------
# Phase B — multiclass_brier
# ---------------------------------------------------------------------------


def _estimates(probabilities: dict[str, float]) -> list[dict[str, Any]]:
    """Build a minimal estimates list from outcome_id -> probability."""
    return [
        {"target_kind": "outcome", "target_id": oid, "final_probability": p}
        for oid, p in probabilities.items()
    ]


def test_multiclass_brier_correct_outcome_zero_when_certain() -> None:
    # P(A)=1.0 and actual=A => sum of squares = (1-1)^2 + (0-0)^2 = 0.0
    estimates = _estimates({"A": 1.0, "B": 0.0})
    assert math.isclose(multiclass_brier(estimates, actual_outcome_id="A"), 0.0, abs_tol=1e-12)


def test_multiclass_brier_wrong_outcome_two_when_certain() -> None:
    # P(A)=1.0 and actual=B => (1-0)^2 + (0-1)^2 = 2.0
    estimates = _estimates({"A": 1.0, "B": 0.0})
    assert math.isclose(multiclass_brier(estimates, actual_outcome_id="B"), 2.0)


def test_multiclass_brier_uniform_binary() -> None:
    # P(A)=0.5, P(B)=0.5, actual=A => (0.5-1)^2 + (0.5-0)^2 = 0.25+0.25 = 0.5
    estimates = _estimates({"A": 0.5, "B": 0.5})
    assert math.isclose(multiclass_brier(estimates, actual_outcome_id="A"), 0.5)


def test_multiclass_brier_skips_non_outcome_rows() -> None:
    # A scenario row should be ignored.
    rows: list[dict[str, Any]] = [
        {"target_kind": "outcome", "target_id": "A", "final_probability": 0.6},
        {"target_kind": "outcome", "target_id": "B", "final_probability": 0.4},
        {"target_kind": "scenario", "target_id": "S1", "final_probability": 0.3},
    ]
    # Only outcome rows: (0.6-1)^2 + (0.4-0)^2 = 0.16 + 0.16 = 0.32
    assert math.isclose(multiclass_brier(rows, actual_outcome_id="A"), 0.32)


# ---------------------------------------------------------------------------
# Phase B — log_score
# ---------------------------------------------------------------------------


def test_log_score_correct_outcome_at_certainty() -> None:
    # -log(1.0) = 0.0
    estimates = _estimates({"A": 1.0, "B": 0.0})
    assert math.isclose(log_score(estimates, actual_outcome_id="A"), 0.0, abs_tol=1e-12)


def test_log_score_correct_outcome_at_half() -> None:
    # -log(0.5) = log(2) ≈ 0.6931
    estimates = _estimates({"A": 0.5, "B": 0.5})
    assert math.isclose(log_score(estimates, actual_outcome_id="A"), math.log(2))


def test_log_score_clips_zero_probability_to_epsilon_floor() -> None:
    # P(B)=0.0 for the actual outcome => score = -log(epsilon_floor)
    epsilon = 1e-9
    estimates = _estimates({"A": 1.0, "B": 0.0})
    expected = -math.log(epsilon)
    assert math.isclose(
        log_score(estimates, actual_outcome_id="B", epsilon_floor=epsilon), expected
    )


def test_log_score_missing_outcome_uses_epsilon_floor() -> None:
    estimates = _estimates({"A": 0.8, "B": 0.2})
    epsilon = 1e-6
    expected = -math.log(epsilon)
    assert math.isclose(
        log_score(estimates, actual_outcome_id="C", epsilon_floor=epsilon), expected
    )


# ---------------------------------------------------------------------------
# Phase B — _clamp_update
# ---------------------------------------------------------------------------


def test_clamp_update_within_range_unchanged() -> None:
    assert math.isclose(_clamp_update(1.5, 3.0), 1.5)  # pyright: ignore[reportPrivateUsage]


def test_clamp_update_positive_clamped() -> None:
    assert math.isclose(_clamp_update(5.0, 3.0), 3.0)  # pyright: ignore[reportPrivateUsage]


def test_clamp_update_negative_clamped() -> None:
    assert math.isclose(_clamp_update(-5.0, 3.0), -3.0)  # pyright: ignore[reportPrivateUsage]


def test_clamp_update_zero_value() -> None:
    assert math.isclose(_clamp_update(0.0, 3.0), 0.0, abs_tol=1e-12)  # pyright: ignore[reportPrivateUsage]


def test_clamp_update_negative_clamp_param_treated_as_absolute() -> None:
    # abs(-2) = 2 is the effective limit
    assert math.isclose(_clamp_update(5.0, -2.0), 2.0)  # pyright: ignore[reportPrivateUsage]
    assert math.isclose(_clamp_update(-5.0, -2.0), -2.0)  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# Phase B — _epsilon_softmax
# ---------------------------------------------------------------------------


def test_epsilon_softmax_uniform_logits_equal_probabilities() -> None:
    probs = _epsilon_softmax([0.0, 0.0, 0.0], 1e-9)  # pyright: ignore[reportPrivateUsage]
    assert len(probs) == 3
    assert all(math.isclose(p, probs[0]) for p in probs)
    assert math.isclose(sum(probs), 1.0, rel_tol=1e-9)


def test_epsilon_softmax_all_probability_sums_to_one() -> None:
    probs = _epsilon_softmax([1.0, 2.0, -1.0], 1e-9)  # pyright: ignore[reportPrivateUsage]
    assert math.isclose(sum(probs), 1.0, rel_tol=1e-9)


def test_epsilon_softmax_min_probability_is_epsilon_floor() -> None:
    eps = 1e-6
    probs = _epsilon_softmax([10.0, 0.0, 0.0], eps)  # pyright: ignore[reportPrivateUsage]
    assert all(p >= eps for p in probs)


def test_epsilon_softmax_highest_logit_gets_highest_probability() -> None:
    probs = _epsilon_softmax([5.0, 1.0, -2.0], 1e-9)  # pyright: ignore[reportPrivateUsage]
    assert probs[0] > probs[1] > probs[2]


def test_epsilon_softmax_empty_logits_returns_empty() -> None:
    assert _epsilon_softmax([], 1e-9) == []  # pyright: ignore[reportPrivateUsage]


def test_epsilon_softmax_k_times_epsilon_ge_one_raises_domain_error() -> None:
    # K=3, epsilon=0.4 => 3*0.4=1.2 >= 1 — must raise ForecastInvalidInput
    with pytest.raises(ForecastInvalidInput) as exc_info:
        _epsilon_softmax([0.0, 0.0, 0.0], 0.4)  # pyright: ignore[reportPrivateUsage]
    assert exc_info.value.code == "epsilon_floor_too_large"


def test_epsilon_softmax_k_times_epsilon_exactly_one_raises_domain_error() -> None:
    # K=2, epsilon=0.5 => 2*0.5=1.0 >= 1
    with pytest.raises(ForecastInvalidInput) as exc_info:
        _epsilon_softmax([0.0, 0.0], 0.5)  # pyright: ignore[reportPrivateUsage]
    assert exc_info.value.code == "epsilon_floor_too_large"


# ---------------------------------------------------------------------------
# Phase B — _percentile_range
# ---------------------------------------------------------------------------


def test_percentile_range_empty_list() -> None:
    result = _percentile_range([])  # pyright: ignore[reportPrivateUsage]
    assert result == {"lo80": 0.0, "hi80": 0.0}


def test_percentile_range_single_value() -> None:
    result = _percentile_range([0.7])  # pyright: ignore[reportPrivateUsage]
    assert math.isclose(float(result["lo80"]), 0.7)
    assert math.isclose(float(result["hi80"]), 0.7)


def test_percentile_range_ordered_correctly() -> None:
    # Uniform distribution 0.0..1.0 in steps of 0.1 (11 values).
    values = [i / 10.0 for i in range(11)]
    result = _percentile_range(values)  # pyright: ignore[reportPrivateUsage]
    # lo80 = p10 index, hi80 = p90 index
    assert float(result["lo80"]) <= float(result["hi80"])
    assert float(result["lo80"]) >= 0.0
    assert float(result["hi80"]) <= 1.0


def test_percentile_range_already_sorted_gives_same_as_unsorted() -> None:
    import random

    values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    shuffled = values[:]
    random.Random(42).shuffle(shuffled)
    assert _percentile_range(values) == _percentile_range(shuffled)  # pyright: ignore[reportPrivateUsage]


def test_percentile_range_known_quantiles() -> None:
    # 10 equal-spaced values [0.0, 0.1, ..., 0.9].
    # len=10, floor(0.10*9)=0 => lo80 = values[0] = 0.0
    # ceil(0.90*9)=9 => hi80 = values[9] = 0.9
    values = [i / 10.0 for i in range(10)]
    result = _percentile_range(values)  # pyright: ignore[reportPrivateUsage]
    assert math.isclose(float(result["lo80"]), 0.0, abs_tol=1e-12)
    assert math.isclose(float(result["hi80"]), 0.9)


# ---------------------------------------------------------------------------
# Phase C — softmax (internal, raises on empty)
# ---------------------------------------------------------------------------


def test_phase_c_softmax_empty_raises_domain_error() -> None:
    with pytest.raises(ForecastInvalidInput) as exc_info:
        _softmax([])  # pyright: ignore[reportPrivateUsage]
    assert exc_info.value.code == "softmax_empty_logits"


def test_phase_c_softmax_sums_to_one() -> None:
    probs = _softmax([1.0, 2.0, 0.5])  # pyright: ignore[reportPrivateUsage]
    assert math.isclose(sum(probs), 1.0, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# Phase C — compute_phase_c_projection: p10 <= p50 <= p90, mean, scenario probs
# ---------------------------------------------------------------------------


def _minimal_phase_c_snapshot(
    *,
    n_claims: int = 2,
    polarity_positive: bool = True,
) -> dict[str, Any]:
    """Build a minimal but valid phase-C snapshot."""
    polarity = 1 if polarity_positive else -1
    claims: list[dict[str, Any]] = [
        {
            "claim_id": f"c{i}",
            "polarity": polarity,
            "evidence_strength": 0.6,
            "reliability_score": 0.7,
        }
        for i in range(n_claims)
    ]
    return {
        "forecast": {
            "forecast_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "question": "Will agent adoption exceed 50% by 2035?",
        },
        "claims": claims,
        "packs": [],
        "dimensions": [
            {
                "dimension_id": "dim-1",
                "metric_id": "adoption_rate",
                "label": "Adoption rate",
                "unit": "%",
                "value_type": "percentage",
                "baseline_year": 2026,
                "baseline_value": 10.0,
                "horizons": [2030, 2035],
            }
        ],
    }


def test_phase_c_scenario_probabilities_sum_to_one() -> None:
    snapshot = _minimal_phase_c_snapshot()
    result = compute_phase_c_projection(snapshot=snapshot)
    total = sum(float(s["probability"]) for s in result["scenarios"])
    assert math.isclose(total, 1.0, rel_tol=1e-9)


def test_phase_c_scenario_probabilities_all_positive() -> None:
    snapshot = _minimal_phase_c_snapshot()
    result = compute_phase_c_projection(snapshot=snapshot)
    for scenario in result["scenarios"]:
        assert float(scenario["probability"]) > 0.0


def test_phase_c_metric_points_p10_le_p50_le_p90() -> None:
    snapshot = _minimal_phase_c_snapshot()
    result = compute_phase_c_projection(snapshot=snapshot)
    for point in result["metric_points"]:
        p10 = float(point["p10"])
        p50 = float(point["p50"])
        p90 = float(point["p90"])
        assert p10 <= p50, f"p10={p10} > p50={p50} in {point}"
        assert p50 <= p90, f"p50={p50} > p90={p90} in {point}"


def test_phase_c_metric_points_mean_between_p10_and_p90() -> None:
    snapshot = _minimal_phase_c_snapshot()
    result = compute_phase_c_projection(snapshot=snapshot)
    for point in result["metric_points"]:
        p10 = float(point["p10"])
        p90 = float(point["p90"])
        mean = float(point["mean"])
        assert p10 <= mean <= p90 + 1e-9, (
            f"mean={mean} outside [{p10}, {p90}] in {point}"
        )


def test_phase_c_composite_p10_le_p50_le_p90() -> None:
    snapshot = _minimal_phase_c_snapshot()
    result = compute_phase_c_projection(snapshot=snapshot)
    for composite in result["composites"]:
        p10 = float(composite["p10"])
        p50 = float(composite["p50"])
        p90 = float(composite["p90"])
        assert p10 <= p50, f"composite p10={p10} > p50={p50}"
        assert p50 <= p90, f"composite p50={p50} > p90={p90}"


def test_phase_c_composite_mean_positive_with_positive_growth() -> None:
    snapshot = _minimal_phase_c_snapshot()
    result = compute_phase_c_projection(snapshot=snapshot)
    assert len(result["composites"]) > 0
    # Sanity: composite mean is positive (baseline=10.0 with positive growth)
    for composite in result["composites"]:
        assert float(composite["mean"]) > 0.0


def test_phase_c_more_support_claims_increases_accelerated_scenario_probability() -> None:
    """Adding more positive claims should increase the accelerated scenario prob."""
    low_support = compute_phase_c_projection(snapshot=_minimal_phase_c_snapshot(n_claims=1))
    high_support = compute_phase_c_projection(
        snapshot=_minimal_phase_c_snapshot(n_claims=8)
    )

    def _prob(result: dict[str, list[dict[str, Any]]], key: str) -> float:
        return next(
            float(s["probability"]) for s in result["scenarios"] if s["label"].startswith(key)
        )

    assert _prob(high_support, "Accelerated") > _prob(low_support, "Accelerated")


def test_phase_c_counter_claims_reduce_accelerated_probability() -> None:
    """Negative-polarity claims should lower the accelerated scenario prob."""
    support_result = compute_phase_c_projection(
        snapshot=_minimal_phase_c_snapshot(n_claims=4, polarity_positive=True)
    )
    counter_result = compute_phase_c_projection(
        snapshot=_minimal_phase_c_snapshot(n_claims=4, polarity_positive=False)
    )

    def _prob(result: dict[str, list[dict[str, Any]]], key: str) -> float:
        return next(
            float(s["probability"]) for s in result["scenarios"] if s["label"].startswith(key)
        )

    assert _prob(counter_result, "Accelerated") < _prob(support_result, "Accelerated")


def test_phase_c_residual_scenario_floor_applied() -> None:
    """Residual scenario must be >= RESIDUAL_FLOOR (0.05) even with heavy support."""
    snapshot = _minimal_phase_c_snapshot(n_claims=20, polarity_positive=True)
    result = compute_phase_c_projection(snapshot=snapshot)
    residual = next(s for s in result["scenarios"] if s["residual_flag"])
    assert float(residual["probability"]) >= 0.05


def test_phase_c_returns_sensitivities() -> None:
    snapshot = _minimal_phase_c_snapshot()
    result = compute_phase_c_projection(snapshot=snapshot)
    assert len(result["sensitivities"]) > 0
    kinds = {row["sensitivity_kind"] for row in result["sensitivities"]}
    assert "scenario_probability" in kinds
    assert "driver_one_way" in kinds


# ---------------------------------------------------------------------------
# Validation: NaN/inf/garbage inputs raise ForecastInvalidInput (task 1 & 2)
# ---------------------------------------------------------------------------


def _phase_b_snapshot_with_relevance(relevance: Any) -> dict[str, Any]:
    """Minimal phase_b snapshot with a single link carrying the given relevance weight."""
    from uuid import uuid4

    oid = str(uuid4())
    return {
        "perturbation_runs": 0,
        "outcomes": [
            {"outcome_id": oid, "normalization_group_id": "ng", "sort_order": 0},
            {
                "outcome_id": str(uuid4()),
                "normalization_group_id": "ng",
                "sort_order": 1,
            },
        ],
        "claims": [
            {
                "claim_id": "c1",
                "polarity": 1,
                "evidence_strength": 0.5,
                "reliability_score": 0.5,
                "cluster_id": "cluster",
                "independence_group": "group",
            }
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": oid,
                "direction": 1,
                "relevance_weight": relevance,
            }
        ],
        "analog_events": [],
        "cross_impact": [],
        "scenarios": [],
    }


@pytest.mark.parametrize(
    "bad_value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        "not_a_number",
        None,
    ],
)
def test_phase_b_nan_inf_relevance_weight_raises_domain_error(bad_value: Any) -> None:
    from api.forecast.probability import compute

    with pytest.raises(ForecastInvalidInput):
        compute(
            snapshot=_phase_b_snapshot_with_relevance(bad_value),
            engine_version="phase_b_v1",
        )


@pytest.mark.parametrize(
    "bad_value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        "not_a_number",
        None,
    ],
)
def test_phase_b_nan_inf_evidence_strength_raises_domain_error(bad_value: Any) -> None:
    from uuid import uuid4

    from api.forecast.probability import compute

    oid = str(uuid4())
    snapshot: dict[str, Any] = {
        "perturbation_runs": 0,
        "outcomes": [
            {"outcome_id": oid, "normalization_group_id": "ng", "sort_order": 0},
            {
                "outcome_id": str(uuid4()),
                "normalization_group_id": "ng",
                "sort_order": 1,
            },
        ],
        "claims": [
            {
                "claim_id": "c1",
                "polarity": 1,
                "evidence_strength": bad_value,
                "reliability_score": 0.5,
                "cluster_id": "cluster",
                "independence_group": "group",
            }
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": oid,
                "direction": 1,
                "relevance_weight": 1.0,
            }
        ],
        "analog_events": [],
        "cross_impact": [],
        "scenarios": [],
    }
    with pytest.raises(ForecastInvalidInput):
        compute(snapshot=snapshot, engine_version="phase_b_v1")


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), float("-inf"), "bad"],
)
def test_phase_a_nan_inf_relevance_weight_raises_domain_error(bad_value: Any) -> None:
    from uuid import uuid4

    from api.forecast.probability import compute

    oid = str(uuid4())
    snapshot: dict[str, Any] = {
        "outcomes": [
            {"outcome_id": oid},
            {"outcome_id": str(uuid4())},
        ],
        "claims": [
            {
                "claim_id": "c1",
                "polarity": 1,
                "evidence_strength": 0.5,
                "reliability_score": 0.5,
                "cluster_id": "cluster",
                "independence_group": "group",
            }
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": oid,
                "direction": 1,
                "relevance_weight": bad_value,
            }
        ],
    }
    with pytest.raises(ForecastInvalidInput):
        compute(snapshot=snapshot, engine_version="phase_a_v1")


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), "bad"],
)
def test_phase_c_nan_inf_evidence_strength_raises_domain_error(bad_value: Any) -> None:
    snapshot = _minimal_phase_c_snapshot()
    snapshot["claims"] = [
        {
            "claim_id": "c1",
            "polarity": 1,
            "evidence_strength": bad_value,
            "reliability_score": 0.7,
        }
    ]
    with pytest.raises(ForecastInvalidInput):
        compute_phase_c_projection(snapshot=snapshot)


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), "bad"],
)
def test_phase_c_nan_inf_reliability_score_raises_domain_error(bad_value: Any) -> None:
    snapshot = _minimal_phase_c_snapshot()
    snapshot["claims"] = [
        {
            "claim_id": "c1",
            "polarity": 1,
            "evidence_strength": 0.5,
            "reliability_score": bad_value,
        }
    ]
    with pytest.raises(ForecastInvalidInput):
        compute_phase_c_projection(snapshot=snapshot)


def test_phase_b_epsilon_softmax_empty_logits_returns_empty() -> None:
    """Empty logit list returns [] without raising (phase_b boundary case)."""
    result = _epsilon_softmax([], 1e-9)  # pyright: ignore[reportPrivateUsage]
    assert result == []


def test_phase_b_epsilon_softmax_k_epsilon_ge_one_raises_domain_error() -> None:
    """K * epsilon_floor >= 1 must raise ForecastInvalidInput, not ValueError."""
    with pytest.raises(ForecastInvalidInput) as exc_info:
        _epsilon_softmax([1.0, 1.0, 1.0], 0.5)  # pyright: ignore[reportPrivateUsage]
    assert exc_info.value.code == "epsilon_floor_too_large"


def test_phase_c_softmax_empty_raises_domain_error_not_value_error() -> None:
    """_softmax([]) must raise ForecastInvalidInput (not bare ValueError -> 500)."""
    with pytest.raises(ForecastInvalidInput) as exc_info:
        _softmax([])  # pyright: ignore[reportPrivateUsage]
    assert exc_info.value.code == "softmax_empty_logits"
    # Confirm it is the exact domain error class (not a raw ValueError)
    assert type(exc_info.value) is ForecastInvalidInput


def test_forecast_invalid_input_has_code_message_details() -> None:
    """ForecastInvalidInput must expose code, message, and details attributes."""
    err = ForecastInvalidInput("test_code", "test message", {"k": "v"})
    assert err.code == "test_code"
    assert err.message == "test message"
    assert err.details == {"k": "v"}
    # It is a ValueError subclass so it propagates naturally
    assert isinstance(err, ValueError)


# ---------------------------------------------------------------------------
# API path: ForecastInvalidInput must map to 4xx (not 500, not 404)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Range validation branch: out-of-range values raise numeric_field_out_of_range
# (task M4a)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [1.5, -0.1, float("inf")],
)
def test_phase_b_evidence_strength_out_of_range_raises_domain_error(bad_value: Any) -> None:
    """evidence_strength is bounded to [0, 1]; values outside raise the range error."""
    from uuid import uuid4

    from api.forecast.probability import compute

    oid = str(uuid4())
    snapshot: dict[str, Any] = {
        "perturbation_runs": 0,
        "outcomes": [
            {"outcome_id": oid, "normalization_group_id": "ng", "sort_order": 0},
            {"outcome_id": str(uuid4()), "normalization_group_id": "ng", "sort_order": 1},
        ],
        "claims": [
            {
                "claim_id": "c1",
                "polarity": 1,
                "evidence_strength": bad_value,
                "reliability_score": 0.5,
                "cluster_id": "cluster",
                "independence_group": "group",
            }
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": oid,
                "direction": 1,
                "relevance_weight": 1.0,
            }
        ],
        "analog_events": [],
        "cross_impact": [],
        "scenarios": [],
    }
    with pytest.raises(ForecastInvalidInput) as exc_info:
        compute(snapshot=snapshot, engine_version="phase_b_v1")
    # 1.5 / -0.1 hit the range branch; inf hits the non-finite branch.
    assert exc_info.value.code in {"numeric_field_out_of_range", "non_finite_numeric_field"}


def test_phase_c_reliability_score_above_one_raises_range_error() -> None:
    """reliability_score is bounded to [0, 1]; > 1 raises numeric_field_out_of_range."""
    snapshot = _minimal_phase_c_snapshot()
    snapshot["claims"] = [
        {
            "claim_id": "c1",
            "polarity": 1,
            "evidence_strength": 0.5,
            "reliability_score": 1.5,
        }
    ]
    with pytest.raises(ForecastInvalidInput) as exc_info:
        compute_phase_c_projection(snapshot=snapshot)
    assert exc_info.value.code == "numeric_field_out_of_range"


def test_phase_b_negative_normalized_weight_raises_range_error() -> None:
    """A negative normalized_weight (< lo=0.0) raises numeric_field_out_of_range."""
    from uuid import uuid4

    from api.forecast.probability import compute

    oid = str(uuid4())
    snapshot: dict[str, Any] = {
        "perturbation_runs": 0,
        "outcomes": [
            {"outcome_id": oid, "normalization_group_id": "ng", "sort_order": 0},
            {"outcome_id": str(uuid4()), "normalization_group_id": "ng", "sort_order": 1},
        ],
        "claims": [
            {
                "claim_id": "c1",
                "polarity": 1,
                "evidence_strength": 0.5,
                "reliability_score": 0.5,
                "cluster_id": "cluster",
                "independence_group": "group",
            }
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": oid,
                "direction": 1,
                "relevance_weight": 1.0,
            }
        ],
        "analog_events": [],
        "cross_impact": [],
        "scenarios": [
            {
                "scenario_id": "s1",
                "outcome_id": oid,
                "validity_status": "valid",
                "normalized_weight": -0.5,
            }
        ],
    }
    with pytest.raises(ForecastInvalidInput) as exc_info:
        compute(snapshot=snapshot, engine_version="phase_b_v1")
    assert exc_info.value.code == "numeric_field_out_of_range"


# ---------------------------------------------------------------------------
# Newly-hardened parse sites: non-finite config/weight/baseline -> domain error
# (task M4b)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), float("-inf"), "bad", None],
)
def test_phase_a_non_finite_epsilon_floor_raises_domain_error(bad_value: Any) -> None:
    """A non-finite snapshot epsilon_floor must raise before the K*eps guard, not 500."""
    from uuid import uuid4

    from api.forecast.probability import compute

    oid = str(uuid4())
    snapshot: dict[str, Any] = {
        "epsilon_floor": bad_value,
        "outcomes": [
            {"outcome_id": oid},
            {"outcome_id": str(uuid4())},
        ],
        "claims": [
            {
                "claim_id": "c1",
                "polarity": 1,
                "evidence_strength": 0.5,
                "reliability_score": 0.5,
                "cluster_id": "cluster",
                "independence_group": "group",
            }
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": oid,
                "direction": 1,
                "relevance_weight": 1.0,
            }
        ],
    }
    with pytest.raises(ForecastInvalidInput):
        compute(snapshot=snapshot, engine_version="phase_a_v1")


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), float("-inf"), "bad", None],
)
def test_phase_b_non_finite_epsilon_floor_raises_domain_error(bad_value: Any) -> None:
    from uuid import uuid4

    from api.forecast.probability import compute

    oid = str(uuid4())
    snapshot: dict[str, Any] = {
        "perturbation_runs": 0,
        "epsilon_floor": bad_value,
        "outcomes": [
            {"outcome_id": oid, "normalization_group_id": "ng", "sort_order": 0},
            {"outcome_id": str(uuid4()), "normalization_group_id": "ng", "sort_order": 1},
        ],
        "claims": [
            {
                "claim_id": "c1",
                "polarity": 1,
                "evidence_strength": 0.5,
                "reliability_score": 0.5,
                "cluster_id": "cluster",
                "independence_group": "group",
            }
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": oid,
                "direction": 1,
                "relevance_weight": 1.0,
            }
        ],
        "analog_events": [],
        "cross_impact": [],
        "scenarios": [],
    }
    with pytest.raises(ForecastInvalidInput):
        compute(snapshot=snapshot, engine_version="phase_b_v1")


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), float("-inf"), "bad", None],
)
def test_phase_b_non_finite_normalized_weight_raises_domain_error(bad_value: Any) -> None:
    """A non-finite normalized_weight must raise rather than corrupt total_weight."""
    from uuid import uuid4

    from api.forecast.probability import compute

    oid = str(uuid4())
    snapshot: dict[str, Any] = {
        "perturbation_runs": 0,
        "outcomes": [
            {"outcome_id": oid, "normalization_group_id": "ng", "sort_order": 0},
            {"outcome_id": str(uuid4()), "normalization_group_id": "ng", "sort_order": 1},
        ],
        "claims": [
            {
                "claim_id": "c1",
                "polarity": 1,
                "evidence_strength": 0.5,
                "reliability_score": 0.5,
                "cluster_id": "cluster",
                "independence_group": "group",
            }
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": oid,
                "direction": 1,
                "relevance_weight": 1.0,
            }
        ],
        "analog_events": [],
        "cross_impact": [],
        "scenarios": [
            {
                "scenario_id": "s1",
                "outcome_id": oid,
                "validity_status": "valid",
                "normalized_weight": bad_value,
            }
        ],
    }
    with pytest.raises(ForecastInvalidInput):
        compute(snapshot=snapshot, engine_version="phase_b_v1")


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), float("-inf"), "bad", None],
)
def test_phase_c_non_finite_baseline_value_raises_domain_error(bad_value: Any) -> None:
    """A non-finite baseline_value must raise ForecastInvalidInput, not produce NaN/500."""
    snapshot = _minimal_phase_c_snapshot()
    snapshot["dimensions"][0]["baseline_value"] = bad_value
    with pytest.raises(ForecastInvalidInput):
        compute_phase_c_projection(snapshot=snapshot)


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), "bad", None],
)
def test_phase_c_non_integer_baseline_year_raises_domain_error(bad_value: Any) -> None:
    """A non-integer baseline_year must raise ForecastInvalidInput, not a bare ValueError (500)."""
    snapshot = _minimal_phase_c_snapshot()
    snapshot["dimensions"][0]["baseline_year"] = bad_value
    with pytest.raises(ForecastInvalidInput):
        compute_phase_c_projection(snapshot=snapshot)


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), "bad", None],
)
def test_phase_c_non_integer_horizon_raises_domain_error(bad_value: Any) -> None:
    snapshot = _minimal_phase_c_snapshot()
    snapshot["dimensions"][0]["horizons"] = [bad_value]
    with pytest.raises(ForecastInvalidInput):
        compute_phase_c_projection(snapshot=snapshot)


@pytest.mark.anyio
async def test_api_forecast_invalid_input_returns_422_not_500(
    tmp_path: object,
) -> None:
    """The router must convert ForecastInvalidInput -> 422, not 500.

    We patch compute_probabilities to raise ForecastInvalidInput directly,
    which simulates what happens when LLM-supplied numerics are NaN/inf.
    """
    from pathlib import Path
    from uuid import UUID

    from httpx import ASGITransport, AsyncClient

    from api.config import Settings
    from api.forecast.artifacts import ForecastArtifactStore
    from api.forecast.dependencies import get_forecast_orchestrator
    from api.forecast.repository import ForecastRepository
    from api.forecast.schemas import ComputeProbabilitiesRequest
    from api.forecast.service import ForecastOrchestrator
    from api.main import create_app
    from api.research.artifacts import ArtifactStore
    from api.research.azure_responses import AzureResponsesClient
    from api.research.dependencies import get_research_orchestrator
    from api.research.repository import ResearchRepository
    from api.research.service import ResearchOrchestrator
    from research_fakes import IntegrationFakeAzure

    path = Path(str(tmp_path))
    settings = Settings(
        research_db_path=path / "test-422.sqlite3",
        research_artifact_dir=path / "research-artifacts",
        forecast_artifact_dir=path / "forecast-artifacts",
        research_poller_enabled=False,
    )
    research = ResearchOrchestrator(
        settings=settings,
        repository=ResearchRepository(settings.research_db_path),
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(AzureResponsesClient, IntegrationFakeAzure()),
    )
    forecast = ForecastOrchestrator(
        settings=settings,
        repository=ForecastRepository(settings.research_db_path),
        artifacts=ForecastArtifactStore(settings.forecast_artifact_dir),
        research_orchestrator=research,
    )

    # Patch compute_probabilities to raise ForecastInvalidInput (simulates NaN input).
    def _raise_invalid(_forecast_id: UUID, _req: ComputeProbabilitiesRequest) -> object:
        raise ForecastInvalidInput(
            "non_finite_numeric_field",
            "Field 'relevance_weight' must be a finite number, got nan",
            {"field": "relevance_weight", "value": "nan"},
        )

    forecast.compute_probabilities = _raise_invalid  # type: ignore[method-assign]

    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will NaN inputs fail gracefully?",
                "resolution_criteria": "Resolve from public evidence.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        response = await client.post(
            f"/forecasts/{forecast_id}/probabilities/compute",
            json={"engine_version": "phase_b_v1"},
        )

    # Must be 422, not 500 and not 404
    assert response.status_code == 422
    detail_body = response.json()["detail"]
    assert isinstance(detail_body, dict)
    detail_typed = cast(dict[str, str], detail_body)
    assert detail_typed["code"] == "non_finite_numeric_field"


@pytest.mark.anyio
async def test_api_projection_invalid_input_returns_422_not_500(
    tmp_path: object,
) -> None:
    """The /projections/compute endpoint must also return 422 for ForecastInvalidInput."""
    from pathlib import Path
    from uuid import UUID

    from httpx import ASGITransport, AsyncClient

    from api.config import Settings
    from api.forecast.artifacts import ForecastArtifactStore
    from api.forecast.dependencies import get_forecast_orchestrator
    from api.forecast.repository import ForecastRepository
    from api.forecast.schemas import ComputeProjectionRequest
    from api.forecast.service import ForecastOrchestrator
    from api.main import create_app
    from api.research.artifacts import ArtifactStore
    from api.research.azure_responses import AzureResponsesClient
    from api.research.dependencies import get_research_orchestrator
    from api.research.repository import ResearchRepository
    from api.research.service import ResearchOrchestrator
    from research_fakes import IntegrationFakeAzure

    path = Path(str(tmp_path))
    settings = Settings(
        research_db_path=path / "test-422-proj.sqlite3",
        research_artifact_dir=path / "research-artifacts",
        forecast_artifact_dir=path / "forecast-artifacts",
        research_poller_enabled=False,
    )
    research = ResearchOrchestrator(
        settings=settings,
        repository=ResearchRepository(settings.research_db_path),
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(AzureResponsesClient, IntegrationFakeAzure()),
    )
    forecast = ForecastOrchestrator(
        settings=settings,
        repository=ForecastRepository(settings.research_db_path),
        artifacts=ForecastArtifactStore(settings.forecast_artifact_dir),
        research_orchestrator=research,
    )

    def _raise_invalid(_forecast_id: UUID, _req: ComputeProjectionRequest) -> object:
        raise ForecastInvalidInput(
            "softmax_empty_logits",
            "Cannot compute softmax over an empty logit list.",
            {},
        )

    forecast.compute_projection = _raise_invalid  # type: ignore[method-assign]

    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will empty logits fail gracefully?",
                "resolution_criteria": "Resolve from public evidence.",
                "forecast_mode": "scenario_projection",
                "projection_dimensions": [
                    {
                        "metric_id": "m1",
                        "label": "Metric",
                        "unit": "%",
                        "value_type": "percentage",
                        "baseline_year": 2026,
                        "baseline_value": 10,
                        "horizons": [2030],
                    }
                ],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        response = await client.post(
            f"/forecasts/{forecast_id}/projections/compute",
        )

    assert response.status_code == 422
    detail_body = response.json()["detail"]
    assert isinstance(detail_body, dict)
    detail_typed = cast(dict[str, str], detail_body)
    assert detail_typed["code"] == "softmax_empty_logits"
