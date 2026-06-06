from __future__ import annotations

from uuid import uuid4

from api.research.schemas import (
    AcceptanceCriterion,
    ExpectedAnswerType,
    ObjectiveContract,
    ResearchItem,
    Severity,
)


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


def build_objective_contract(
    *,
    user_prompt: str,
) -> tuple[ObjectiveContract, list[ResearchItem], str]:
    criteria = [
        AcceptanceCriterion(
            criterion_id="AC-001",
            description="Directly answers every user-requested question.",
            verification_method="semantic_answer",
            severity=Severity.BLOCKER,
            required_evidence_type=["answer"],
            generated_by="user_prompt",
            confidence=90,
        ),
        AcceptanceCriterion(
            criterion_id="AC-002",
            description="Supports decision-critical claims with trustworthy citations.",
            verification_method="citation_required",
            severity=Severity.MAJOR,
            required_evidence_type=["official", "primary", "high_authority"],
            confidence=85,
        ),
        AcceptanceCriterion(
            criterion_id="AC-003",
            description="States explicit dates for facts that may change over time.",
            verification_method="freshness_check",
            severity=Severity.MAJOR,
            required_evidence_type=["dated_source"],
            required_freshness="current when applicable",
            confidence=80,
        ),
        AcceptanceCriterion(
            criterion_id="AC-004",
            description="Identifies contradictions, uncertainty, assumptions, and limitations.",
            verification_method="risk_review",
            severity=Severity.MAJOR,
            required_evidence_type=["limitation_statement"],
            confidence=80,
        ),
        AcceptanceCriterion(
            criterion_id="AC-005",
            description=(
                "Provides clear recommendations or implications proportional to the evidence."
            ),
            verification_method="semantic_answer",
            severity=Severity.MINOR,
            required_evidence_type=["synthesis"],
            confidence=80,
        ),
    ]
    contract = ObjectiveContract(
        contract_id=f"OC-{uuid4()}",
        original_user_prompt=user_prompt,
        normalized_objective=user_prompt.strip(),
        task_type="mixed_source_research",
        acceptance_criteria=criteria,
        source_policy={
            "priority": [
                "official",
                "regulator",
                "peer_reviewed",
                "filing",
                "reputable_industry_analysis",
                "reputable_news",
            ]
        },
        freshness_policy={"state_dates_for_volatile_facts": True},
        security_policy={
            "public_web_search_allowed": True,
        },
        output_requirements=[
            "Executive summary",
            "Detailed findings",
            "Evidence table",
            "Risks and limitations",
            "Source citations",
            "Recommendations or implications",
        ],
        contract_confidence=85,
        contract_frozen=True,
    )
    items = [
        ResearchItem(
            item_id=f"RI-{index:03d}",
            criterion_id=criterion.criterion_id,
            question=criterion.description,
            expected_answer_type=_expected_answer_type(criterion.verification_method),
            severity=criterion.severity,
        )
        for index, criterion in enumerate(criteria, start=1)
    ]
    prompt, _ = build_optimized_prompt(user_prompt=user_prompt)
    prompt += f"""

# Objective Contract
contract_id: {contract.contract_id}

# Research Items
{chr(10).join(f"- {item.item_id} ({item.severity.value}): {item.question}" for item in items)}

# Rerun-Compatible Output Requirement
Structure the report so later item-scoped updates can be appended without rewriting
unrelated findings. Use clear section headings and preserve source metadata.
"""
    return contract, items, prompt


def _expected_answer_type(method: str) -> ExpectedAnswerType:
    if method in {"citation_required", "freshness_check", "source_quality_check"}:
        return ExpectedAnswerType.FACT
    if method == "risk_review":
        return ExpectedAnswerType.RISK
    return ExpectedAnswerType.SYNTHESIS
