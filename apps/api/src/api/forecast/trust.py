from __future__ import annotations

from typing import cast
from uuid import UUID

from api.forecast.errors import ForecastConflict
from api.forecast.repository import ForecastRepository
from api.forecast.schemas import PackRole, ToolProfile
from api.research.schemas import utc_now


def ensure_trusted_sources_allowed(
    repository: ForecastRepository,
    *,
    identifiers: list[str],
    tool_profile: ToolProfile,
    pack_role: PackRole,
    tool_names: list[str] | None = None,
    vector_store_ids: list[str] | None = None,
    mcp_server_ids: list[str] | None = None,
) -> None:
    if not identifiers:
        if tool_profile == ToolProfile.PRIVATE:
            raise ForecastConflict(
                "trusted_source_required",
                "Private Forecast packs require approved trusted source identifiers.",
            )
        return
    for identifier in identifiers:
        row = repository.get_trusted_source(identifier)
        if row is None:
            raise ForecastConflict(
                "trusted_source_unknown",
                "Trusted source identifier is not approved.",
                {"identifier": identifier},
            )
        if row["status"] != "approved":
            raise ForecastConflict(
                "trusted_source_not_approved",
                "Trusted source identifier is not approved.",
                {"identifier": identifier, "status": row["status"]},
            )
        expires_at = row["expires_at"]
        if expires_at:
            from datetime import datetime

            parsed = datetime.fromisoformat(expires_at)
            if parsed <= utc_now():
                raise ForecastConflict(
                    "trusted_source_expired",
                    "Trusted source identifier is expired.",
                    {"identifier": identifier},
                )
        allowed_profiles = set(_json_list(row["allowed_profiles_json"]))
        if allowed_profiles and tool_profile.value not in allowed_profiles:
            raise ForecastConflict(
                "trusted_source_profile_not_allowed",
                "Trusted source does not allow this tool profile.",
                {"identifier": identifier, "tool_profile": tool_profile.value},
            )
        allowed_roles = set(_json_list(row["allowed_pack_roles_json"]))
        if allowed_roles and pack_role.value not in allowed_roles:
            raise ForecastConflict(
                "trusted_source_pack_role_not_allowed",
                "Trusted source does not allow this pack role.",
                {"identifier": identifier, "pack_role": pack_role.value},
            )
        allowed_tool_names = set(_json_list(row["allowed_tool_names_json"]))
        requested_tool_names = set(tool_names or [])
        if allowed_tool_names and not requested_tool_names.issubset(allowed_tool_names):
            raise ForecastConflict(
                "trusted_source_tool_not_allowed",
                "Trusted source does not allow the resolved Forecast tools.",
                {
                    "identifier": identifier,
                    "allowed_tool_names": sorted(allowed_tool_names),
                    "requested_tool_names": sorted(requested_tool_names),
                },
            )
        allowed_vector_store_ids = set(_json_list(row["allowed_vector_store_ids_json"]))
        requested_vector_store_ids = set(vector_store_ids or [])
        if (
            tool_profile == ToolProfile.PRIVATE
            and "file_search" in requested_tool_names
            and not allowed_vector_store_ids
        ):
            raise ForecastConflict(
                "trusted_source_vector_store_not_allowed",
                "Trusted source must explicitly allow private vector stores.",
                {
                    "identifier": identifier,
                    "allowed_vector_store_ids": [],
                    "requested_vector_store_ids": sorted(requested_vector_store_ids),
                },
            )
        if allowed_vector_store_ids and not requested_vector_store_ids.issubset(
            allowed_vector_store_ids
        ):
            raise ForecastConflict(
                "trusted_source_vector_store_not_allowed",
                "Trusted source does not allow the requested vector stores.",
                {
                    "identifier": identifier,
                    "allowed_vector_store_ids": sorted(allowed_vector_store_ids),
                    "requested_vector_store_ids": sorted(requested_vector_store_ids),
                },
            )
        allowed_mcp_server_ids = set(_json_list(row["allowed_mcp_server_ids_json"]))
        requested_mcp_server_ids = set(mcp_server_ids or [])
        if allowed_mcp_server_ids and not requested_mcp_server_ids.issubset(
            allowed_mcp_server_ids
        ):
            raise ForecastConflict(
                "trusted_source_mcp_server_not_allowed",
                "Trusted source does not allow the requested MCP servers.",
                {
                    "identifier": identifier,
                    "allowed_mcp_server_ids": sorted(allowed_mcp_server_ids),
                    "requested_mcp_server_ids": sorted(requested_mcp_server_ids),
                },
            )


def ensure_reviewer_for_action(action: str, reviewer: str | None) -> None:
    if action in {
        "approve_private_data_use",
        "approve_probability_publication",
        "override_probability_with_reason",
        "approve_external_report",
        "approve_trusted_source",
    } and not (reviewer and reviewer.strip()):
        raise ForecastConflict(
            "reviewer_required",
            "Reviewer is required for this Forecast review action.",
        )


def require_policy_tools_match(
    repository: ForecastRepository,
    *,
    policy_decision_id: UUID,
    resolved_tools: list[dict[str, object]],
) -> None:
    import json

    policy = repository.get_policy_decision(policy_decision_id)
    policy_tools = json.loads(policy["resolved_tools_json"] or "[]")
    if policy_tools != resolved_tools:
        raise ForecastConflict(
            "policy_tools_mismatch",
            "Resolved tools changed after Forecast policy approval.",
            {
                "policy_decision_id": str(policy_decision_id),
                "policy_tools": policy_tools,
                "resolved_tools": resolved_tools,
            },
        )


def _json_list(value: str | None) -> list[str]:
    import json

    if not value:
        return []
    loaded = json.loads(value)
    if not isinstance(loaded, list):
        return []
    items = cast(list[object], loaded)
    return [str(item) for item in items]
