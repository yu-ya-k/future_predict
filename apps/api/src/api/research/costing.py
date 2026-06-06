from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from api.research.extractors import response_to_jsonable
from api.research.schemas import CostEvent


def build_cost_event(
    *,
    step: str,
    model: str,
    response_id: str | None,
    response: Any,
    tool_calls: int,
    input_cost_per_1m: float,
    output_cost_per_1m: float,
    tool_call_cost: float,
    billable_tool_calls: int | None = None,
) -> CostEvent:
    raw = response_to_jsonable(response)
    usage = _usage_payload(raw)
    input_tokens = _int_value(usage, "input_tokens", "prompt_tokens")
    output_tokens = _int_value(usage, "output_tokens", "completion_tokens")
    charged_tool_calls = tool_calls if billable_tool_calls is None else billable_tool_calls
    estimated_cost = (
        (input_tokens / 1_000_000) * input_cost_per_1m
        + (output_tokens / 1_000_000) * output_cost_per_1m
        + charged_tool_calls * tool_call_cost
    )

    return CostEvent(
        step=step,
        model=model,
        response_id=response_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=tool_calls,
        estimated_cost_usd=estimated_cost,
    )


def count_billable_web_search_calls(tool_calls: list[Any]) -> int:
    return sum(1 for tool_call in tool_calls if _is_web_search_tool_call(tool_call))


def _usage_payload(raw: dict[str, Any]) -> dict[str, Any]:
    usage = raw.get("usage")
    if isinstance(usage, dict):
        return cast(dict[str, Any], usage)
    return raw


def _int_value(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def _is_web_search_tool_call(tool_call: Any) -> bool:
    if isinstance(tool_call, dict):
        mapping = cast(Mapping[str, Any], tool_call)
        value = mapping.get("type", "")
    else:
        value = getattr(tool_call, "type", "")
    return "web_search" in str(value)
