from __future__ import annotations

import re
from dataclasses import dataclass

from api.research.schemas import ContextClassification

CONFIDENTIAL_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        r"\b(?:api[_-]?key|secret|token|password)\b\s*[:=]",
        r"https?://(?:intranet|internal|localhost|127\.0\.0\.1)[^\s]*",
        r"\b(?:confidential|internal only|internal)\b",
        r"(?:社外秘|社内|機密|秘密)",
    )
]


@dataclass(frozen=True)
class PublicToolPolicy:
    web_search_enabled: bool
    reason: str


def contains_confidential_text(text: str) -> bool:
    return any(pattern.search(text) for pattern in CONFIDENTIAL_PATTERNS)


def build_public_tool_policy(
    *,
    context_classification: ContextClassification,
    contains_confidential_context: bool,
    web_search_allowed: bool,
    tool_owner: str,
) -> PublicToolPolicy:
    if not web_search_allowed:
        return PublicToolPolicy(
            web_search_enabled=False,
            reason=f"{tool_owner}_web_search_disabled_by_run_option",
        )

    if contains_confidential_context:
        return PublicToolPolicy(
            web_search_enabled=False,
            reason=f"{tool_owner}_web_search_blocked_confidential_detected",
        )

    if context_classification in {"internal", "confidential", "mixed"}:
        return PublicToolPolicy(
            web_search_enabled=False,
            reason=f"{tool_owner}_web_search_blocked_{context_classification}_context",
        )

    return PublicToolPolicy(
        web_search_enabled=True,
        reason=f"{tool_owner}_web_search_enabled_public_context",
    )


def should_enable_reviewer_web_search(
    *,
    context_classification: ContextClassification,
    contains_confidential_context: bool,
    web_search_allowed: bool,
) -> bool:
    return build_public_tool_policy(
        context_classification=context_classification,
        contains_confidential_context=contains_confidential_context,
        web_search_allowed=web_search_allowed,
        tool_owner="reviewer",
    ).web_search_enabled


def should_enable_deep_research_web_search(
    *,
    context_classification: ContextClassification,
    contains_confidential_context: bool,
    web_search_allowed: bool,
) -> bool:
    return build_public_tool_policy(
        context_classification=context_classification,
        contains_confidential_context=contains_confidential_context,
        web_search_allowed=web_search_allowed,
        tool_owner="deep_research",
    ).web_search_enabled


def redact_public_tool_input(text: str) -> tuple[str, dict[str, int]]:
    replacements = {
        "emails": r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "secrets": r"\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*\S+",
        "internal_urls": r"https?://(?:intranet|internal|localhost|127\.0\.0\.1)[^\s]*",
    }
    redacted = text
    summary: dict[str, int] = {}

    for key, pattern in replacements.items():
        redacted, count = re.subn(pattern, f"[REDACTED_{key.upper()}]", redacted, flags=re.I)
        summary[key] = count

    return redacted, summary
