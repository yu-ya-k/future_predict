from __future__ import annotations

from api.research.schemas import ContextClassification


def build_optimized_prompt(
    *,
    user_prompt: str,
    context_classification: ContextClassification,
) -> tuple[str, list[str]]:
    acceptance_criteria = [
        "ユーザーの必須質問に直接回答している",
        "主要な主張に出典または根拠がある",
        "最新性が必要な事実では日付を明示している",
        "不確実性、前提、限界を明示している",
        "結論が根拠から過剰に飛躍していない",
    ]
    if context_classification in {"internal", "confidential", "mixed"}:
        acceptance_criteria.append("public claims と internal/confidential claims を混同していない")

    optimized_prompt = f"""# Research Objective
{user_prompt}

# Required Deliverables
- Executive summary
- Detailed findings
- Evidence table
- Key assumptions
- Risks and limitations
- Source citations
- Final recommendations or implications

# Source Priority
1. Official sources
2. Regulators / public institutions
3. Peer-reviewed research
4. Company filings / official announcements
5. Reputable industry analysis
6. Reputable news sources
7. Other sources only when clearly labeled

# Citation Requirement
Provide inline citations and source metadata.
Do not cite sources you did not use.
State dates explicitly when facts may change.

# Analysis Requirement
Avoid generic summaries.
Compare evidence.
Identify contradictions.
Explain confidence and limitations.

# Output Language
Japanese unless the user explicitly requests another language.
"""
    return optimized_prompt, acceptance_criteria
