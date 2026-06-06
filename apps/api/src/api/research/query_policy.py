from __future__ import annotations

import re
from typing import cast

from api.research.schemas import ContextClassification, QueryPolicyDecision

SENSITIVE_PATTERNS = [
    re.compile(r"\b(?:api[_-]?key|access[_-]?token|secret|password|passwd)\b", re.I),
    re.compile(r"\b(?:internal|confidential)\s+(?:project|codename|code\s*name)\b", re.I),
    re.compile(r"\b(?:project|codename|code\s*name)\s+[A-Z][A-Za-z0-9_-]{2,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b[A-Za-z0-9_]*TOKEN[A-Za-z0-9_]*\s*=", re.I),
    re.compile(r"\b[A-Za-z0-9_]*SECRET[A-Za-z0-9_]*\s*=", re.I),
]


def contains_sensitive_terms(text: str) -> bool:
    return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


def query_policy_gate(
    plan: dict[str, object],
    state: dict[str, object],
) -> QueryPolicyDecision:
    context = str(state.get("context_classification") or ContextClassification.PUBLIC.value)
    if context in {
        ContextClassification.INTERNAL.value,
        ContextClassification.CONFIDENTIAL.value,
        ContextClassification.MIXED.value,
    }:
        return QueryPolicyDecision(
            status="blocked",
            safe_queries=[],
            blocked_reason=f"Public web search is blocked for {context} context.",
        )

    if bool(plan.get("contains_sensitive_terms")):
        return QueryPolicyDecision(
            status="blocked",
            safe_queries=[],
            blocked_reason="Search query contains sensitive terms.",
        )

    candidate_queries = plan.get("candidate_queries")
    if not isinstance(candidate_queries, list):
        return QueryPolicyDecision(
            status="blocked",
            safe_queries=[],
            blocked_reason="No candidate queries were provided.",
        )

    candidate_query_values = cast(list[object], candidate_queries)
    safe_queries = [
        str(query) for query in candidate_query_values if str(query).strip()
    ]
    if not safe_queries:
        return QueryPolicyDecision(
            status="blocked",
            safe_queries=[],
            blocked_reason="No safe non-empty queries were provided.",
        )

    return QueryPolicyDecision(status="allowed", safe_queries=safe_queries)
