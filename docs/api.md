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

Runs use public-web Deep Research by default. The query policy gate still runs
before public-web Deep Research submission and targeted verification, and blocks
queries that contain sensitive terms.

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

## Manual Import

`POST /research-runs/manual-import` imports a Deep Research prompt and report
that were produced manually in ChatGPT. The request must be
`multipart/form-data`.

Fields:

- `input_prompt_file` or `input_prompt_text`, exactly one.
- `report_file` or `report_text`, exactly one.
- `allow_remote_review`, required boolean. When false, the run stops in human
  review without calling the reviewer, finalizer, or verifier.
- `allow_api_reruns`, required boolean. When false, targeted and full API reruns
  are forced to zero for the imported run.
- `rerun_execution_mode`, optional: `api`, `manual_chatgpt`, or `disabled`.
  When omitted, legacy behavior is preserved:
  `allow_api_reruns=true` maps to `api`, and `false` maps to `disabled`.
  If `allow_api_reruns=false`, an explicit `api` mode is also normalized to
  `disabled`.
  `manual_chatgpt` requires `allow_api_reruns=true`; it keeps rerun limits but
  pauses after rerun planning for a ChatGPT prompt/result upload instead of
  submitting Deep Research through the API.
- `options_json`, optional JSON matching `ResearchRunOptions`.
- `idempotency_key`, optional. Repeating the same key with the same request
  returns the existing run; the same key with different content returns `409`.

Only `.md` and `.txt` uploads are accepted. Files must be UTF-8, non-blank, and
within `RESEARCH_MANUAL_IMPORT_MAX_FILE_BYTES`. Prompt text is capped at 50,000
characters. Report text is capped by `RESEARCH_MANUAL_IMPORT_MAX_REPORT_CHARS`.
The API is the source of truth for these limits and rejects oversized requests
with `422`. The web app performs client-side checks that mirror the shipped
defaults, so operators who change the server-side manual import limits should
keep the web deployment aligned or expect the server to enforce the final
decision.

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/manual-import \
  -F input_prompt_file=@prompt.md \
  -F report_file=@report.md \
  -F allow_remote_review=true \
  -F allow_api_reruns=true \
  -F rerun_execution_mode=manual_chatgpt \
  -F idempotency_key=operator-ticket-1234
```

Manual import creates Deep Research attempt `1` with
`source="manual_upload"`, `model="chatgpt-deep-research-manual"`,
`status="completed"`, and no Responses API `response_id`. Imported URLs and
Markdown links are saved as unverified manual citations. Prompt/report bodies
are saved as artifacts; idempotency metadata stores hashes, lengths, filenames,
content types, and import time, not the raw bodies.

When a `manual_chatgpt` import later needs targeted or full rerun, human review
returns `pending_manual_rerun` and `allowed_actions=[]`. Run the prompt manually
in ChatGPT, then upload exactly one of `report_text` or `report_file`:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/manual-rerun-result \
  -F rerun_id=RR-... \
  -F report_file=@rerun-result.md
```

Targeted uploads are merged as item-scoped deltas; full-rerun uploads replace
the current report. A stale `rerun_id`, missing pending prompt, non-manual
import, or targeted upload that looks like a full merged report returns `409`.
The endpoint can also return `409` if the report changed after prompt creation
or if the same `rerun_id` was already accepted with different content.
Re-uploading the same accepted `rerun_id` with the same body is idempotent.

If the generated rerun prompt is blocked by query policy, the run stops in
human review with `done_reason="manual_rerun_prompt_blocked_by_query_policy"`
and does not expose `pending_manual_rerun`. Operators must change course from
the human-review/audit context; there is no ChatGPT prompt to run or upload for
that blocked plan.

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

## Checkpoints And Forks

Checkpoint endpoints expose persisted phase-boundary snapshots. They use
`research_checkpoints` as the source of truth; older runs without checkpoint
rows do not infer checkpoints from audit history.

List checkpoints for a run:

```sh
curl -sS 'http://127.0.0.1:8000/research-runs/{run_id}/checkpoints?include_forks=true'
```

Response shape:

```json
{
  "run_id": "00000000-0000-0000-0000-000000000000",
  "checkpoints": [
    {
      "checkpoint_id": "cp_001",
      "run_id": "00000000-0000-0000-0000-000000000000",
      "checkpoint_no": 1,
      "kind": "deep_research_collected",
      "node_anchor": "deep_research_attempt_1",
      "forkable": true,
      "source_attempt_no": 1,
      "source_review_no": null,
      "source_response_id": "resp_deep_1",
      "report_hash": "sha256:...",
      "created_at": "2026-06-06T04:45:00Z",
      "forks": []
    }
  ]
}
```

Fetch one checkpoint, including its saved snapshot:

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/checkpoints/{checkpoint_id}
```

Previewing is required before creating a fork:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/checkpoints/{checkpoint_id}/fork-preview \
  -H 'Content-Type: application/json' \
  -d '{"additional_prompt":"Re-run from this point with 2026 assumptions."}'
```

The preview returns the composed prompt, query-policy decision, source prompt
and report excerpts, warnings, and `preview_hash`. Submit must include the same
additional prompt, a caller-generated `idempotency_key`, and the confirmed
preview hash:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/checkpoints/{checkpoint_id}/forks \
  -H 'Content-Type: application/json' \
  -d '{
    "additional_prompt": "Re-run from this point with 2026 assumptions.",
    "idempotency_key": "client-generated-key",
    "confirmed_preview_hash": "sha256:..."
  }'
```

If the preview hash no longer matches, submit returns `409`. A non-forkable
checkpoint also returns `409`. Repeating the same submit with the same
`idempotency_key` returns the same child run and must not submit a second remote
Deep Research request.

A forked run is independent of the parent. The child copies the contract,
research items, source prompt, and source report snapshot into child-local state
and artifacts. Child counters, cost events, and tool-call counts start at zero
and include only child execution; the source snapshot is not counted as a child
Deep Research attempt. The parent is not modified by previewing or submitting a
fork.

If query policy blocks the fork prompt, submit still creates or returns an
auditable child run in `needs_human_review` with
`done_reason=fork_deep_research_blocked_by_query_policy`; no remote Deep
Research request is made.

Lineage is readable from the child even if the parent is later deleted:

```sh
curl -sS http://127.0.0.1:8000/research-runs/{child_run_id}/lineage
```

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
  "warnings": [],
  "pending_manual_rerun": null
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
  poller. For `manual_chatgpt` manual imports, this creates a pending ChatGPT
  rerun prompt instead.
- `request_full_rerun`: submit a full replacement Deep Research rerun, or create
  a pending ChatGPT rerun prompt for `manual_chatgpt` manual imports.
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
- `GET /research-runs/{run_id}/checkpoints`
- `GET /research-runs/{run_id}/checkpoints/{checkpoint_id}`
- `GET /research-runs/{run_id}/lineage`

## Forecast PhaseA API

All Forecast endpoints use the same API-key behavior as `/research-runs`.
Command endpoints accept `Idempotency-Key`. Reusing a completed key with the
same request replays the stored response. Reusing the key with different request
content returns `409 idempotency_conflict`; reusing a key while the original
command is still reserved returns `409 idempotency_in_progress`. Conflict
responses use typed detail:

```json
{
  "code": "framing_not_approved",
  "message": "Approve the latest framing before dispatching research packs.",
  "details": {}
}
```

Required PhaseA `409` codes include `forecast_disabled`,
`framing_not_approved`, `forecast_already_started`, `policy_blocked`,
`policy_requires_revision`, `pack_not_completed`, `evidence_not_ready`,
`scenarios_not_ready`, `claim_targets_not_approved`,
`draft_estimate_set_exists`, `approval_required`,
`estimate_set_already_committed`, `forecast_already_resolved`,
`idempotency_conflict`, and `idempotency_in_progress`.

Lifecycle:

- `POST /forecasts` returns `202` and creates a forecast plus framing version.
- `POST /forecasts/{id}/review` with `{"action":"approve_framing"}` freezes the
  latest question/outcome framing.
- `POST /forecasts/{id}/research-packs` dispatches the public `current_state`
  pack after framing approval.
- `POST /forecasts/{id}/evidence/extract` requires a completed pack and stores
  only source-linked public claims.
- `POST /forecasts/{id}/scenarios/generate` requires evidence and creates
  outcome-bound scenarios.
- `POST /forecasts/{id}/probabilities/compute` requires scenarios and approved
  outcome claim-target links. If the current canonical input snapshot matches an
  existing draft, the existing draft is returned. A different snapshot while a
  draft exists returns `409 draft_estimate_set_exists`.
- `POST /forecasts/{id}/review` with
  `{"action":"approve_phase_a_version","estimate_set_id":"..."}` records human
  approval for that draft only.
- `POST /forecasts/{id}/versions/commit` requires approval and freezes the draft
  into a version with canonical snapshot bytes and hash.
- `POST /forecasts/{id}/resolve` requires a committed version and rejects a
  second resolution with `409 forecast_already_resolved`.

Read endpoints:

- `GET /forecasts`
- `GET /forecasts/{id}`
- `GET /forecasts/{id}/audit`

Forecast-linked ResearchRuns are protected. If
`forecast_research_packs.research_run_id` references the run, deletion returns
`409 forecast_linked_research_run`; retain both Research artifacts and Forecast
version artifacts for reproducibility.

PhaseA read APIs return public sources and claims only. Private data approval,
private packs, reforecasting, and narrative-only scenarios are PhaseB/C work and
are not available in PhaseA.

## Storage Compatibility

Startup performs SQLite compatibility migrations for manual ChatGPT reruns. An
existing `research_runs` table gains `rerun_execution_mode` with default `api`,
and pending/accepted manual rerun upload state is stored in
`manual_rerun_requests` with at most one active pending request per run.

## Public Interface Summary

- `POST /research-runs`
- `POST /research-runs/manual-import`
- `POST /research-runs/{run_id}/manual-rerun-result`
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
- `GET /research-runs/{run_id}/checkpoints`
- `GET /research-runs/{run_id}/checkpoints/{checkpoint_id}`
- `POST /research-runs/{run_id}/checkpoints/{checkpoint_id}/fork-preview`
- `POST /research-runs/{run_id}/checkpoints/{checkpoint_id}/forks`
- `GET /research-runs/{run_id}/lineage`
