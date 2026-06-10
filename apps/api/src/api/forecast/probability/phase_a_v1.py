from __future__ import annotations

import hashlib
import inspect
import json
import math
from collections import defaultdict
from typing import Any, cast

ENGINE_VERSION = "phase_a_v1"
SCORER_VERSION = "phase_a_scorer_v1"
RANDOM_SEED = 0
DEFAULT_EPSILON_FLOOR = 1e-9
DEFAULT_KAPPA = 1.0
DEFAULT_CLAMP = 3.0


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


def compute_phase_a_estimates(
    *,
    snapshot: dict[str, Any],
    epsilon_floor: float | None = None,
    kappa: float | None = None,
    clamp: float | None = None,
) -> list[dict[str, Any]]:
    epsilon_floor = (
        float(snapshot.get("epsilon_floor", DEFAULT_EPSILON_FLOOR))
        if epsilon_floor is None
        else epsilon_floor
    )
    kappa = (
        float(snapshot.get("kappa", DEFAULT_KAPPA)) if kappa is None else kappa
    )
    clamp = (
        float(snapshot.get("clamp", DEFAULT_CLAMP)) if clamp is None else clamp
    )

    outcomes = snapshot["outcomes"]
    links = snapshot["approved_target_links"]
    claims = {claim["claim_id"]: claim for claim in snapshot["claims"]}
    target_logits = {outcome["outcome_id"]: 0.0 for outcome in outcomes}

    grouped: dict[tuple[str, str, str, int], list[float]] = defaultdict(list)
    for link in links:
        if link["target_kind"] != "outcome":
            continue
        claim = claims.get(link["claim_id"])
        if claim is None:
            continue
        contribution = (
            float(link["relevance_weight"])
            * float(claim["evidence_strength"])
            * float(claim["reliability_score"])
        )
        key = (
            str(claim["cluster_id"]),
            str(link["target_kind"]),
            str(link["target_id"]),
            int(claim.get("polarity", 1)) * int(link["direction"]),
        )
        grouped[key].append(contribution)

    by_target_direction_group: dict[tuple[str, int, str], list[float]] = defaultdict(list)
    for (cluster_id, _kind, target_id, direction), values in grouped.items():
        claim = next(
            (
                item
                for item in claims.values()
                if item["cluster_id"] == cluster_id
            ),
            None,
        )
        independence_group = str(claim["independence_group"]) if claim else cluster_id
        by_target_direction_group[(target_id, direction, independence_group)].append(max(values))

    for (target_id, direction, _independence_group), values in by_target_direction_group.items():
        target_logits[target_id] = target_logits.get(target_id, 0.0) + (
            direction * (sum(values) / len(values))
        )

    ordered_logits = [target_logits[str(outcome["outcome_id"])] * kappa for outcome in outcomes]
    clamped_logits = [max(-clamp, min(clamp, value)) for value in ordered_logits]
    probabilities = _epsilon_softmax(clamped_logits, epsilon_floor)
    prior = 1.0 / len(outcomes)

    estimates: list[dict[str, Any]] = []
    for outcome, raw_logit, clamped_logit, probability in zip(
        outcomes,
        ordered_logits,
        clamped_logits,
        probabilities,
        strict=True,
    ):
        estimates.append(
            {
                "target_kind": "outcome",
                "target_id": outcome["outcome_id"],
                "prior": prior,
                "evidence_update": clamped_logit,
                "cross_impact_adjustment": 0.0,
                "simulation_adjustment": 0.0,
                "calibration_adjustment": 0.0,
                "human_adjustment": 0.0,
                "final_probability": probability,
                "uncertainty_range": {
                    "lo80": max(epsilon_floor, probability - 0.10),
                    "hi80": min(1.0, probability + 0.10),
                },
                "components": {
                    "pre_clamp_delta_logit": raw_logit,
                    "clamped": raw_logit != clamped_logit,
                    "kappa": kappa,
                    "clamp": clamp,
                    "epsilon_floor": epsilon_floor,
                    "cross_impact_engine": "none",
                    "random_seed": RANDOM_SEED,
                },
            }
        )
    return estimates


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


def _epsilon_softmax(logits: list[float], epsilon_floor: float) -> list[float]:
    if not logits:
        return []
    if len(logits) * epsilon_floor >= 1:
        raise ValueError("K*epsilon_floor must be < 1.")
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
        compute_phase_a_estimates,
        multiclass_brier,
        log_score,
        _epsilon_softmax,
        _stable,
    ]
    return {
        "constants": {
            "ENGINE_VERSION": ENGINE_VERSION,
            "SCORER_VERSION": SCORER_VERSION,
            "RANDOM_SEED": RANDOM_SEED,
            "DEFAULT_EPSILON_FLOOR": DEFAULT_EPSILON_FLOOR,
            "DEFAULT_KAPPA": DEFAULT_KAPPA,
            "DEFAULT_CLAMP": DEFAULT_CLAMP,
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
