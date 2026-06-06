# API

The research API is mounted under `/research-runs`. This document describes the
target v2 contract for the Deep Research Review Orchestrator.

Examples assume the API is running at `http://127.0.0.1:8000`.

## Create A Run

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs \
  -H 'Content-Type: application/json' \
  -d '{
    "user_prompt": "Research the market outlook for battery recycling in Japan.",
    "context_classification": "public",
    "options": {
      "max_targeted_rerun_runs": 2,
      "max_full_rerun_runs": 1,
      "max_llm_patch_runs": 3,
      "max_verification_runs": 2,
      "max_total_iterations": 8,
      "max_total_tool_calls": 120
    }
  }'
```

`context_classification` is required and must be one of:

- `public`
- `internal`
- `confidential`
- `mixed`

Only `public` runs can submit public-web Deep Research or targeted verification
automatically. `internal`, `confidential`, and `mixed` runs create the
ObjectiveContract / ResearchItems, then stop for human review until a private
tool or redaction path is available.

`options` is optional. Defaults come from API settings. The v2 API rejects
legacy options including `max_deep_research_runs` and
`max_no_progress_rounds`.

Response status is `202 Accepted`:

```json
{
  "run_id": "00000000-0000-0000-0000-000000000000",
  "thread_id": "11111111-1111-1111-1111-111111111111",
  "status": "waiting_deep_research",
  "created_at": "2026-01-01T00:00:00Z"
}
```

## Status And Progress

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}
```

The response includes run status, human-review state, Deep Research submission
timestamp, and item-level progress:

```json
{
  "run_id": "00000000-0000-0000-0000-000000000000",
  "status": "reviewing",
  "terminal_status": null,
  "done_reason": null,
  "needs_human_review": false,
  "deep_research_submitted_at": "2026-06-06T04:30:00Z",
  "progress": {
    "items_total": 12,
    "items_answered": 8,
    "items_partial": 2,
    "items_unanswered": 1,
    "items_unverifiable": 1,
    "blockers_unresolved": 1,
    "latest_verdict": "needs_targeted_rerun",
    "latest_score": 78,
    "deep_research_runs": 1,
    "targeted_rerun_runs": 1,
    "full_rerun_runs": 0,
    "llm_patch_runs": 0,
    "verification_runs": 2,
    "total_reviews": 2,
    "total_tool_calls": 32,
    "estimated_cost_usd": 0.42
  }
}
```

## Contract

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/contract
```

Returns the frozen `ObjectiveContract`:

```json
{
  "run_id": "00000000-0000-0000-0000-000000000000",
  "contract": {
    "contract_id": "OC-00000000-0000-0000-0000-000000000000",
    "original_user_prompt": "Research the market outlook for battery recycling in Japan.",
    "normalized_objective": "Assess the market outlook for battery recycling in Japan.",
    "task_type": "mixed_source_research",
    "acceptance_criteria": [
      {
        "criterion_id": "AC-001",
        "description": "Directly answers every user-requested question.",
        "verification_method": "semantic_answer",
        "severity": "blocker",
        "required_evidence_type": ["answer"],
        "required_freshness": null,
        "generated_by": "user_prompt",
        "confidence": 90
      },
      {
        "criterion_id": "AC-002",
        "description": "Supports decision-critical claims with trustworthy citations.",
        "verification_method": "citation_required",
        "severity": "major",
        "required_evidence_type": ["official", "primary", "high_authority"],
        "required_freshness": null,
        "generated_by": "task_template",
        "confidence": 85
      },
      {
        "criterion_id": "AC-003",
        "description": "States explicit dates for facts that may change over time.",
        "verification_method": "freshness_check",
        "severity": "major",
        "required_evidence_type": ["dated_source"],
        "required_freshness": "current when applicable",
        "generated_by": "freshness_policy",
        "confidence": 80
      },
      {
        "criterion_id": "AC-004",
        "description": "Identifies contradictions, uncertainty, assumptions, and limitations.",
        "verification_method": "risk_review",
        "severity": "major",
        "required_evidence_type": ["limitation_statement"],
        "required_freshness": null,
        "generated_by": "task_template",
        "confidence": 80
      },
      {
        "criterion_id": "AC-005",
        "description": "Provides clear recommendations or implications proportional to the evidence.",
        "verification_method": "semantic_answer",
        "severity": "minor",
        "required_evidence_type": ["synthesis"],
        "required_freshness": null,
        "generated_by": "task_template",
        "confidence": 80
      }
    ],
    "source_policy": {
      "priority": [
        "official",
        "regulator",
        "peer_reviewed",
        "filing",
        "reputable_industry_analysis",
        "reputable_news"
      ]
    },
    "freshness_policy": {
      "state_dates_for_volatile_facts": true
    },
    "security_policy": {
      "context_classification": "public",
      "public_web_search_allowed": true
    },
    "output_requirements": [
      "Executive summary",
      "Detailed findings",
      "Evidence table",
      "Risks and limitations",
      "Source citations",
      "Recommendations or implications"
    ],
    "explicit_out_of_scope": [],
    "contract_confidence": 85,
    "contract_frozen": true
  }
}
```

## Research Items

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/items
```

Returns the current lightweight item ledger:

```json
{
  "run_id": "00000000-0000-0000-0000-000000000000",
  "items": [
    {
      "item_id": "RI-001",
      "criterion_id": "AC-001",
      "question": "What is the current Japan battery recycling market size?",
      "expected_answer_type": "market_size",
      "status": "partial",
      "severity": "blocker",
      "confidence": 64,
      "evidence_summary": "One industry source gives estimates, but no official source was found.",
      "citation_ids": ["CIT-001"],
      "failure_mode": "needs_different_sources",
      "failure_mode_confidence": 82,
      "unresolved_reason": "Needs official or regulator source.",
      "tried_queries": ["Japan battery recycling market size"],
      "tried_source_types": ["industry_report"]
    }
  ]
}
```

## Rerun Plans

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/rerun-plans
```

Returns targeted rerun history:

```json
{
  "run_id": "00000000-0000-0000-0000-000000000000",
  "rerun_plans": [
    {
      "rerun_id": "RR-001",
      "scope": "targeted_gap_closure",
      "target_item_ids": ["RI-001"],
      "preserve_item_ids": ["RI-002", "RI-003"],
      "output_mode": "delta_sections_only",
      "max_tool_calls": 15,
      "rerun_reason": "Blocker item needs official-source evidence."
    }
  ]
}
```

## Report

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/report
```

`final_report` is populated when the run is complete. `report` contains the
latest candidate report even when review or rerun is still pending. Automated
`finalize_with_limitation` adds limitation warnings; human
`approve_with_limitation` can complete with the reviewer comment and may not add
new warning text.

## Audit

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/audit
```

The audit response contains:

- `attempts`
- `reviews`
- `objective_contract`
- `research_items`
- `rerun_plans`
- `verification_queries`
- `citations`
- `tool_calls`
- `cost_events`
- `human_decisions`
- `history`

## Human Review Queue

```sh
curl -sS http://127.0.0.1:8000/research-runs/human-reviews
```

Each queue item includes the run id, run status, done reason, latest verdict,
latest score, latest rationale, created/updated timestamps, and audit summary.

## Human Review Payload

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/human-review
```

The payload includes the latest report, latest review, unresolved items, audit
summary, warnings, reason, and allowed actions:

```json
{
  "run_id": "00000000-0000-0000-0000-000000000000",
  "reason": "Blocker item remains unresolved",
  "latest_report": "...",
  "latest_review": null,
  "unresolved_items": [
    {
      "item_id": "RI-001",
      "question": "What is the current Japan battery recycling market size?",
      "severity": "blocker",
      "status": "partial",
      "failure_mode": "likely_not_publicly_available",
      "failure_mode_confidence": 72,
      "unresolved_reason": "No reliable public source found after one verification attempt."
    }
  ],
  "allowed_actions": [
    "approve",
    "approve_with_limitation",
    "request_review",
    "request_llm_patch",
    "request_verification",
    "request_targeted_rerun",
    "request_item_revision",
    "reject"
  ],
  "audit_summary": {
    "deep_research_runs": 1,
    "targeted_rerun_runs": 1,
    "full_rerun_runs": 0,
    "llm_patch_runs": 0,
    "verification_runs": 1,
    "total_reviews": 2,
    "no_progress_count": 0,
    "total_tool_calls": 24,
    "estimated_cost_usd": 0.31
  },
  "warnings": []
}
```

If the run is not waiting for human review, the endpoint returns `409`.

## Resume From Human Review

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{
    "action": "request_targeted_rerun",
    "comment": "Search for official regulator and filing sources for RI-001."
  }'
```

Actions:

- `approve`: finalize the latest report and complete the run with
  `terminal_status=completed_by_human_approval`.
- `approve_with_limitation`: finalize the latest report with limitations.
- `request_review`: retry the reviewer step after reviewer timeout or schema /
  request failure.
- `request_llm_patch`: ask the reviewer deployment for a bounded report patch,
  then review again.
- `request_verification`: run targeted verification after query policy allows
  the safe query.
- `request_targeted_rerun`: submit item-scoped Deep Research and wait for the
  poller.
- `request_item_revision`: keep the run in human review for manual ResearchItem
  revision.
- `reject`: fail the run with `done_reason=human_rejected`.

The legacy `request_deep_research` action is invalid in v2.
Continuing actions can return `409` when a guard has already been reached.
Only one resume decision can claim a waiting human-review run.

## Cancel

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/cancel
```

If the run is waiting on a remote Deep Research response, the orchestrator tries
to cancel that response. The local run is marked `cancelled` with
`done_reason=cancelled_by_user`.

## Delete

```sh
curl -sS -X DELETE http://127.0.0.1:8000/research-runs/{run_id}
```

Successful deletion returns `204 No Content`. If the run is not terminal, the
orchestrator first runs the same remote cancellation used by `/cancel`; if that
remote cancellation fails, deletion returns `409`. After deletion, run-specific
endpoints return `404`.

## Diagnostic Endpoints

- `GET /research-runs/{run_id}/contract`
- `GET /research-runs/{run_id}/items`
- `GET /research-runs/{run_id}/rerun-plans`
- `GET /research-runs/{run_id}/reviews`
- `GET /research-runs/{run_id}/audit`
- `GET /research-runs/{run_id}/citations`
- `GET /research-runs/{run_id}/attempts`
- `GET /research-runs/{run_id}/tool-calls`
- `GET /research-runs/{run_id}/cost-events`
- `GET /research-runs/{run_id}/human-decisions`

## Public Interface Summary

- `POST /research-runs`
- `GET /research-runs/{run_id}`
- `GET /research-runs/{run_id}/contract`
- `GET /research-runs/{run_id}/items`
- `GET /research-runs/{run_id}/rerun-plans`
- `GET /research-runs/{run_id}/reviews`
- `GET /research-runs/{run_id}/audit`
- `GET /research-runs/{run_id}/report`
- `GET /research-runs/human-reviews`
- `GET /research-runs/{run_id}/human-review`
- `POST /research-runs/{run_id}/resume`
- `POST /research-runs/{run_id}/cancel`
- `DELETE /research-runs/{run_id}`
