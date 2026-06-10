from __future__ import annotations

import hashlib
from dataclasses import dataclass

from api.forecast.schemas import (
    ConfidentialityClass,
    PackRole,
    ResearchPackRequest,
    ToolProfile,
)

DEFAULT_PHASE_B_PACKS: tuple[tuple[PackRole, ToolProfile], ...] = (
    (PackRole.CURRENT_STATE, ToolProfile.PUBLIC),
    (PackRole.BASE_RATE, ToolProfile.PUBLIC),
    (PackRole.DRIVERS, ToolProfile.PUBLIC),
    (PackRole.COUNTER_EVIDENCE, ToolProfile.PUBLIC),
    (PackRole.SIGNALS, ToolProfile.PUBLIC),
)


@dataclass(frozen=True)
class ResolvedForecastTools:
    tools: list[dict[str, object]]
    vector_store_ids: list[str]
    mcp_server_ids: list[str]


def default_pack_requests() -> list[ResearchPackRequest]:
    return [
        ResearchPackRequest(pack_role=pack_role, tool_profile=tool_profile)
        for pack_role, tool_profile in DEFAULT_PHASE_B_PACKS
    ]


def resolve_forecast_tools(request: ResearchPackRequest) -> ResolvedForecastTools:
    if request.tool_profile == ToolProfile.SYNTHESIS:
        return ResolvedForecastTools(tools=[], vector_store_ids=[], mcp_server_ids=[])
    if request.tool_profile == ToolProfile.PUBLIC:
        return ResolvedForecastTools(
            tools=[{"type": "web_search_preview"}],
            vector_store_ids=[],
            mcp_server_ids=[],
        )
    return ResolvedForecastTools(
        tools=[{"type": "file_search", "vector_store_ids": request.vector_store_ids}],
        vector_store_ids=request.vector_store_ids,
        mcp_server_ids=request.mcp_server_ids,
    )


def cache_key_for_pack(
    *,
    forecast_id: str,
    pack_role: PackRole,
    tool_profile: ToolProfile,
    data_classification: ConfidentialityClass,
    prompt_hash: str,
) -> str:
    payload = "|".join(
        [
            forecast_id,
            pack_role.value,
            tool_profile.value,
            data_classification.value,
            prompt_hash,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
