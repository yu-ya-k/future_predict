from __future__ import annotations

import hashlib
import inspect
import json
import math
import random
from collections import defaultdict
from typing import Any, cast

from api.forecast.errors import ForecastInvalidInput

ENGINE_VERSION = "phase_b_v1"
SCORER_VERSION = "phase_b_scorer_v1"
RANDOM_SEED = 0
DEFAULT_EPSILON_FLOOR = 1e-9
DEFAULT_KAPPA_EVIDENCE = 1.0
DEFAULT_KAPPA_CROSS_IMPACT = 1.0
DEFAULT_CLAMP = 3.0
DEFAULT_PERTURBATION_RUNS = 200


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


def compute_phase_b_estimates(
    *,
    snapshot: dict[str, Any],
    epsilon_floor: float | None = None,
    kappa_evidence: float | None = None,
    kappa_cross_impact: float | None = None,
    clamp: float | None = None,
    random_seed: int | None = None,
    perturbation_runs: int | None = None,
) -> list[dict[str, Any]]:
    epsilon_floor = (
        _parse_finite_float(
            snapshot.get("epsilon_floor", DEFAULT_EPSILON_FLOOR),
            field="epsilon_floor",
            lo=0.0,
        )
        if epsilon_floor is None
        else epsilon_floor
    )
    kappa_evidence = (
        _parse_finite_float(
            snapshot.get("kappa_evidence", DEFAULT_KAPPA_EVIDENCE),
            field="kappa_evidence",
        )
        if kappa_evidence is None
        else kappa_evidence
    )
    kappa_cross_impact = (
        _parse_finite_float(
            snapshot.get("kappa_cross_impact", DEFAULT_KAPPA_CROSS_IMPACT),
            field="kappa_cross_impact",
        )
        if kappa_cross_impact is None
        else kappa_cross_impact
    )
    clamp = (
        _parse_finite_float(snapshot.get("clamp", DEFAULT_CLAMP), field="clamp")
        if clamp is None
        else clamp
    )
    random_seed = (
        int(snapshot.get("random_seed", RANDOM_SEED))
        if random_seed is None
        else random_seed
    )
    perturbation_runs = (
        int(snapshot.get("perturbation_runs", DEFAULT_PERTURBATION_RUNS))
        if perturbation_runs is None
        else perturbation_runs
    )

    outcomes = sorted(
        snapshot["outcomes"],
        key=lambda item: (
            str(item.get("normalization_group_id", "")),
            int(item.get("sort_order", 0)),
            str(item["outcome_id"]),
        ),
    )
    analog_weights = _analog_weights(snapshot)
    evidence_delta = _evidence_delta(snapshot)

    outcomes_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for outcome in outcomes:
        outcomes_by_group[str(outcome.get("normalization_group_id") or "default")].append(outcome)

    priors_by_outcome: dict[str, float] = {}
    for group_outcomes in outcomes_by_group.values():
        priors_by_outcome.update(_priors(group_outcomes, analog_weights))
    cross_impact_delta = _cross_impact_delta(snapshot, priors_by_outcome)

    outcome_estimates: list[dict[str, Any]] = []
    outcome_probabilities: dict[str, float] = {}
    outcome_probability_samples: dict[str, list[float]] = {}
    for group_id, group_outcomes in outcomes_by_group.items():
        priors = {
            str(outcome["outcome_id"]): priors_by_outcome[str(outcome["outcome_id"])]
            for outcome in group_outcomes
        }
        logits: list[float] = []
        raw_logits: list[float] = []
        for outcome in group_outcomes:
            outcome_id = str(outcome["outcome_id"])
            prior = priors[outcome_id]
            evidence = evidence_delta.get(outcome_id, 0.0)
            cross = cross_impact_delta.get(outcome_id, 0.0)
            prior_logit = math.log(max(prior, epsilon_floor))
            raw_update = kappa_evidence * evidence + kappa_cross_impact * cross
            applied_update = _clamp_update(raw_update, clamp)
            raw_logit = prior_logit + raw_update
            logit = prior_logit + applied_update
            raw_logits.append(raw_logit)
            logits.append(logit)
        probabilities = _epsilon_softmax(logits, epsilon_floor)
        probability_samples = _perturbation_probability_samples(
            logits=logits,
            seed=random_seed + len(outcome_estimates),
            epsilon_floor=epsilon_floor,
            runs=perturbation_runs,
        )
        ranges = [_percentile_range(samples) for samples in probability_samples]
        for outcome, raw_logit, clamped_logit, probability, uncertainty in zip(
            group_outcomes,
            raw_logits,
            logits,
            probabilities,
            ranges,
            strict=True,
        ):
            outcome_id = str(outcome["outcome_id"])
            outcome_probabilities[outcome_id] = probability
            outcome_probability_samples[outcome_id] = probability_samples[
                group_outcomes.index(outcome)
            ]
            outcome_estimates.append(
                {
                    "target_kind": "outcome",
                    "target_id": outcome_id,
                    "prior": priors[outcome_id],
                    "evidence_update": kappa_evidence * evidence_delta.get(outcome_id, 0.0),
                    "cross_impact_adjustment": (
                        kappa_cross_impact * cross_impact_delta.get(outcome_id, 0.0)
                    ),
                    "simulation_adjustment": 0.0,
                    "calibration_adjustment": 0.0,
                    "human_adjustment": 0.0,
                    "final_probability": probability,
                    "uncertainty_range": uncertainty,
                    "components": {
                        "normalization_group_id": group_id,
                        "analog_weight": analog_weights.get(outcome_id, 0.0),
                        "pre_clamp_logit": raw_logit,
                        "applied_logit": clamped_logit,
                        "pre_clamp_update": (
                            kappa_evidence * evidence_delta.get(outcome_id, 0.0)
                            + kappa_cross_impact * cross_impact_delta.get(outcome_id, 0.0)
                        ),
                        "applied_update": _clamp_update(
                            kappa_evidence * evidence_delta.get(outcome_id, 0.0)
                            + kappa_cross_impact * cross_impact_delta.get(outcome_id, 0.0),
                            clamp,
                        ),
                        "clamped": raw_logit != clamped_logit,
                        "kappa_evidence": kappa_evidence,
                        "kappa_cross_impact": kappa_cross_impact,
                        "clamp": clamp,
                        "epsilon_floor": epsilon_floor,
                        "random_seed": random_seed,
                        "perturbation_runs": perturbation_runs,
                        "cross_impact_engine": "single_pass_source_prior_v1",
                    },
                }
            )

    scenario_estimates = _scenario_estimates(
        snapshot=snapshot,
        outcome_probabilities=outcome_probabilities,
        outcome_probability_samples=outcome_probability_samples,
        random_seed=random_seed,
        epsilon_floor=epsilon_floor,
    )
    return outcome_estimates + scenario_estimates


def multiclass_brier(
    estimates: list[dict[str, Any]],
    *,
    actual_outcome_id: str,
) -> float:
    return sum(
        (
            float(estimate["final_probability"])
            - (1.0 if str(estimate["target_id"]) == actual_outcome_id else 0.0)
        )
        ** 2
        for estimate in estimates
        if estimate["target_kind"] == "outcome"
    )


def log_score(
    estimates: list[dict[str, Any]],
    *,
    actual_outcome_id: str,
    epsilon_floor: float = DEFAULT_EPSILON_FLOOR,
) -> float:
    probability = next(
        (
            float(estimate["final_probability"])
            for estimate in estimates
            if estimate["target_kind"] == "outcome"
            and str(estimate["target_id"]) == actual_outcome_id
        ),
        epsilon_floor,
    )
    return -math.log(max(probability, epsilon_floor))


def _priors(
    outcomes: list[dict[str, Any]],
    analog_weights: dict[str, float],
) -> dict[str, float]:
    alpha = {
        str(outcome["outcome_id"]): 1.0 + analog_weights.get(str(outcome["outcome_id"]), 0.0)
        for outcome in outcomes
    }
    total = math.fsum(alpha.values())
    return {outcome_id: value / total for outcome_id, value in alpha.items()}


def _analog_weights(snapshot: dict[str, Any]) -> dict[str, float]:
    weights: dict[str, float] = defaultdict(float)
    for event in snapshot.get("analog_events", []):
        if event.get("active", True):
            weights[str(event["matched_outcome_id"])] += max(
                0.0,
                _parse_finite_float(event["weight"], field="analog_event.weight"),
            )
    return dict(weights)


def _evidence_delta(snapshot: dict[str, Any]) -> dict[str, float]:
    claims = {str(claim["claim_id"]): claim for claim in snapshot.get("claims", [])}
    grouped: dict[tuple[str, str, str, int], list[float]] = defaultdict(list)
    for link in snapshot.get("approved_target_links", []):
        if link["target_kind"] != "outcome":
            continue
        claim = claims.get(str(link["claim_id"]))
        if claim is None:
            continue
        effective_direction = int(claim.get("polarity", 1)) * int(link["direction"])
        contribution = (
            _parse_finite_float(link["relevance_weight"], field="relevance_weight", lo=0.0)
            * _parse_finite_float(
                claim["evidence_strength"], field="evidence_strength", lo=0.0, hi=1.0
            )
            * _parse_finite_float(
                claim["reliability_score"], field="reliability_score", lo=0.0, hi=1.0
            )
        )
        grouped[
            (
                str(claim["cluster_id"]),
                str(link["target_id"]),
                str(claim["independence_group"]),
                effective_direction,
            )
        ].append(contribution)

    by_target_direction_group: dict[tuple[str, int, str], list[float]] = defaultdict(list)
    for (_cluster_id, target_id, independence_group, direction), values in grouped.items():
        by_target_direction_group[(target_id, direction, independence_group)].append(max(values))

    delta: dict[str, float] = defaultdict(float)
    for (target_id, direction, _independence_group), values in by_target_direction_group.items():
        delta[target_id] += direction * (sum(values) / len(values))
    return dict(delta)


def _cross_impact_delta(
    snapshot: dict[str, Any],
    source_probabilities: dict[str, float],
) -> dict[str, float]:
    delta: dict[str, float] = defaultdict(float)
    for impact in snapshot.get("cross_impact", []):
        source_outcome_id = impact.get("source_outcome_id")
        source_probability = (
            source_probabilities.get(str(source_outcome_id), 0.0)
            if source_outcome_id is not None
            else 1.0
        )
        delta[str(impact["target_outcome_id"])] += (
            source_probability * _parse_finite_float(impact["delta"], field="cross_impact.delta")
        )
    return dict(delta)


def _scenario_estimates(
    *,
    snapshot: dict[str, Any],
    outcome_probabilities: dict[str, float],
    outcome_probability_samples: dict[str, list[float]],
    random_seed: int,
    epsilon_floor: float,
) -> list[dict[str, Any]]:
    scenarios_by_outcome: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in snapshot.get("scenarios", []):
        if scenario.get("validity_status", "valid") == "valid":
            scenarios_by_outcome[str(scenario["outcome_id"])].append(scenario)

    estimates: list[dict[str, Any]] = []
    for outcome_id, scenarios in scenarios_by_outcome.items():
        total_weight = math.fsum(
            _parse_finite_float(
                item.get("normalized_weight", 0.0),
                field="normalized_weight",
                lo=0.0,
            )
            for item in scenarios
        )
        if total_weight <= 0:
            continue
        for scenario in scenarios:
            weight = _parse_finite_float(
                scenario.get("normalized_weight", 0.0),
                field="normalized_weight",
                lo=0.0,
            )
            probability = outcome_probabilities.get(outcome_id, 0.0) * weight / total_weight
            weight_share = weight / total_weight
            outcome_samples = outcome_probability_samples.get(outcome_id, [])
            uncertainty_range = (
                _percentile_range([sample * weight_share for sample in outcome_samples])
                if outcome_samples
                else {"lo80": probability, "hi80": probability}
            )
            estimates.append(
                {
                    "target_kind": "scenario",
                    "target_id": scenario["scenario_id"],
                    "prior": outcome_probabilities.get(outcome_id, 0.0),
                    "evidence_update": 0.0,
                    "cross_impact_adjustment": 0.0,
                    "simulation_adjustment": 0.0,
                    "calibration_adjustment": 0.0,
                    "human_adjustment": 0.0,
                    "final_probability": probability,
                    "uncertainty_range": uncertainty_range,
                    "components": {
                        "derived_from_outcome_id": outcome_id,
                        "normalized_weight": weight,
                        "weight_share": weight_share,
                        "outcome_weight_total": total_weight,
                        "random_seed": random_seed,
                        "range_source": "outcome_perturbation",
                    },
                }
            )
    return estimates


def _clamp_update(value: float, clamp: float) -> float:
    limit = abs(clamp)
    return max(-limit, min(limit, value))


def _perturbation_probability_samples(
    *,
    logits: list[float],
    seed: int,
    epsilon_floor: float,
    runs: int,
) -> list[list[float]]:
    if runs <= 0:
        probabilities = _epsilon_softmax(logits, epsilon_floor)
        return [[value] for value in probabilities]
    generator = random.Random(seed)
    samples: list[list[float]] = [[] for _ in logits]
    for _ in range(runs):
        perturbed = [value + generator.uniform(-0.15, 0.15) for value in logits]
        for index, probability in enumerate(_epsilon_softmax(perturbed, epsilon_floor)):
            samples[index].append(probability)
    return samples


def _percentile_range(values: list[float]) -> dict[str, float]:
    if not values:
        return {"lo80": 0.0, "hi80": 0.0}
    ordered = sorted(values)
    return {
        "lo80": ordered[max(0, math.floor(0.10 * (len(ordered) - 1)))],
        "hi80": ordered[min(len(ordered) - 1, math.ceil(0.90 * (len(ordered) - 1)))],
    }


def _epsilon_softmax(logits: list[float], epsilon_floor: float) -> list[float]:
    if not logits:
        return []
    if len(logits) * epsilon_floor >= 1:
        raise ForecastInvalidInput(
            "epsilon_floor_too_large",
            f"K*epsilon_floor must be < 1, got K={len(logits)} epsilon_floor={epsilon_floor}",
            {"k": len(logits), "epsilon_floor": epsilon_floor},
        )
    max_logit = max(logits)
    exps = [math.exp(value - max_logit) for value in logits]
    total = math.fsum(exps)
    softmax = [value / total for value in exps]
    scale = 1.0 - len(logits) * epsilon_floor
    return [epsilon_floor + scale * value for value in softmax]


def _engine_hash_payload() -> dict[str, Any]:
    functions = [
        canonical_json_bytes,
        snapshot_hash,
        compute_phase_b_estimates,
        multiclass_brier,
        log_score,
        _priors,
        _analog_weights,
        _evidence_delta,
        _cross_impact_delta,
        _scenario_estimates,
        _clamp_update,
        _perturbation_probability_samples,
        _percentile_range,
        _epsilon_softmax,
        _stable,
    ]
    return {
        "constants": {
            "ENGINE_VERSION": ENGINE_VERSION,
            "SCORER_VERSION": SCORER_VERSION,
            "RANDOM_SEED": RANDOM_SEED,
            "DEFAULT_EPSILON_FLOOR": DEFAULT_EPSILON_FLOOR,
            "DEFAULT_KAPPA_EVIDENCE": DEFAULT_KAPPA_EVIDENCE,
            "DEFAULT_KAPPA_CROSS_IMPACT": DEFAULT_KAPPA_CROSS_IMPACT,
            "DEFAULT_CLAMP": DEFAULT_CLAMP,
            "DEFAULT_PERTURBATION_RUNS": DEFAULT_PERTURBATION_RUNS,
        },
        "functions": {
            function.__name__: inspect.getsource(function) for function in functions
        },
    }


def _stable(value: Any) -> Any:
    if isinstance(value, float):
        return float(format(value, ".17g"))
    if isinstance(value, dict):
        mapped = cast(dict[object, object], value)
        return {str(key): _stable(mapped[key]) for key in sorted(mapped, key=str)}
    if isinstance(value, list):
        return [_stable(item) for item in cast(list[object], value)]
    return value
