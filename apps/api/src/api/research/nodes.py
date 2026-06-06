from __future__ import annotations


def build_optimized_prompt(
    *,
    user_prompt: str,
) -> tuple[str, list[str]]:
    acceptance_criteria = [
        "Directly answers the user's required questions.",
        "Supports key claims with citations or clearly stated evidence.",
        "States dates explicitly for facts that may change over time.",
        "Clearly states uncertainty, assumptions, and limitations.",
        "Keeps conclusions proportional to the evidence.",
    ]

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
Write the entire Deep Research output in English.
If the user's prompt is written in another language, preserve its meaning but translate
the deliverables into English.
Do not switch to another output language unless this application explicitly overrides
this instruction.
"""
    return optimized_prompt, acceptance_criteria
