# Research Orchestrator v2

The Research Orchestrator is implemented in `apps/api/src/api/research`. The v2
design changes the unit of repair from a full report to unresolved
`ResearchItem` records. Deep Research still performs broad initial research and
targeted follow-up research, while the reviewer model owns structured review,
small patches, finalization, and the single routing gate.

## Design Principle

Deep Research is not rerun to rewrite a report. It is rerun only to close
specific unresolved ResearchItems with the smallest safe action.

The v2 orchestrator therefore keeps a lightweight item ledger as the P0 quality
surface:

- `ObjectiveContract`: frozen interpretation of the user's objective.
- `AcceptanceCriterion`: stable criteria used by review and rerun planning.
- `ResearchItem`: item-level research questions derived from the criteria.
- `ReviewResult` / `ReviewRecord`: item assessments and a single verdict.
- `RerunPlan`: target item ids, preserved item ids, source hints, and tool-call
  budget for a targeted rerun.
- `PatchDelta`: deterministic merge primitive used for item-scoped deltas.
- `verification_queries`: query-policy decisions for targeted verification.

Claim and full evidence ledgers are intentionally out of P0. They can be added
after item decomposition, failure-mode diagnosis, and targeted rerun quality are
validated with evals.

## Workflow

1. `POST /research-runs` creates a run from the user prompt and optional guard
   limits.
2. The orchestrator validates query policy, builds an `ObjectiveContract`,
   generates initial `ResearchItem` rows, freezes the contract, and submits an
   Azure OpenAI Responses background request for initial Deep Research.
3. The API stores the run as `waiting_deep_research` with the pending response
   id.
4. `ResearchPoller` collects completed Deep Research responses when
   `RESEARCH_POLLER_ENABLED=true`.
5. Collection stores the candidate report, citations, tool calls, cost events,
   and raw response artifacts. Item-level evidence remains summarized on
   `ResearchItem` records in P0.
6. Review uses structured output matching `ReviewResult`. The review gate
   returns item-level status, failure mode, confidence, and recommended action.
7. Deterministic routing uses only the latest `ReviewRecord` plus guard state.
8. LLM patches are bounded reviewer edits and then re-enter review.
9. Verification uses public web search only after the query policy gate allows
   the generated safe query. Blocked verification goes to human review.
10. Targeted reruns submit a `RerunPlan` and require Deep Research to return
    item deltas, not a full merged report. Application code applies the delta
    through deterministic merge and rejects outputs that look like a full merged
    report.
11. Full rerun is reserved for defective contracts, unusable initial reports, or
    systemic source failure.
12. Human review is a resumable control point. Payloads include unresolved
    items, failure modes, attempted actions, and allowed next actions.
13. Phase-boundary checkpoints are saved for the execution-flow UI. A user can
    preview and then fork from a forkable checkpoint into an independent child
    run without mutating the parent.

## Checkpoint Forks

Checkpoint rows are persisted in `research_checkpoints`; the API does not infer
them from audit history. Automatic checkpoints are written at major boundaries:
Deep Research collection, review recording, LLM patch application, verification
completion, human-review stop, and finalization. Writes use a dedupe key so
poller retries and stale-claim recovery do not create duplicate checkpoints.

Forking is preview-confirm, never one click. Preview composes the fork prompt
from the source prompt, source report snapshot, checkpoint metadata, and required
additional user instructions. It also returns the query-policy decision and a
`preview_hash`. Submit requires a non-empty additional prompt, an idempotency
key, and the confirmed preview hash; stale hashes return `409`.

A child run is independent of the parent. It copies the contract, items, source
prompt, and source report snapshot into child-local state and saves lineage in
`research_run_lineage`. Child counters, costs, and tool-call totals start at
zero and include only the child execution. The checkpoint seed report is not
counted as a child Deep Research attempt and is not overwritten when child
attempt 1 is collected.

If query policy blocks the fork prompt, no remote Deep Research call is made.
The child is still created for auditability in `needs_human_review` with
`done_reason=fork_deep_research_blocked_by_query_policy`.

Deleting or cancelling a parent does not affect child runs. Parent deletion may
remove parent artifacts, but child lineage remains readable from the child-local
snapshot.

## Query Policy

The query policy gate runs before public-web Deep Research submission and
targeted verification. It blocks generated queries that contain sensitive
terms. Verification decisions are persisted as `verification_queries` with raw
query, safe query, policy decision, and blocked reason.

## Review Verdicts

`ReviewResult.verdict` can return:

- `pass`: finalize the current report.
- `needs_llm_patch`: apply item-scoped report deltas without new external
  research.
- `needs_verification`: run targeted verification if query policy allows it.
- `needs_targeted_rerun`: submit Deep Research for unresolved item ids only.
- `needs_full_rerun`: rebuild the research attempt because the current report or
  contract is unusable.
- `needs_item_revision`: split, merge, or clarify ResearchItems before more
  automation.
- `finalize_with_limitation`: complete with explicit limitations when unresolved
  non-blocking items remain.
- `human_review`: stop automation and require a reviewer decision.

The legacy `needs_deep_research` verdict is invalid in v2.

## Failure Modes

Each `ItemAssessment` must include one failure mode:

- `none`
- `format_only`
- `in_report_but_lost`
- `needs_targeted_verification`
- `needs_different_sources`
- `needs_deeper_search`
- `needs_query_reformulation`
- `source_contradiction`
- `likely_not_publicly_available`
- `criterion_too_ambiguous`
- `requires_human_judgment`

Routing is based on failure mode, severity, confidence, security policy, and
budget guards. Score is retained only as diagnostic metadata.

## Routing Priority

The single review gate chooses the route in this order:

1. `pass`
2. explicit `human_review`
3. security violation
4. hard stops
5. item revision
6. LLM patch
7. verification
8. targeted rerun
9. full rerun
10. finalize with limitation
11. human review fallback

Default item routing:

| Failure mode | Minor | Major | Blocker |
| --- | --- | --- | --- |
| `format_only` | `needs_llm_patch` | `needs_llm_patch` | `needs_llm_patch` |
| `in_report_but_lost` | `needs_llm_patch` | `needs_llm_patch` | `needs_llm_patch` |
| `needs_targeted_verification` | `needs_verification` | `needs_verification` | `needs_verification` |
| `needs_different_sources` | `needs_targeted_rerun` | `needs_targeted_rerun` | `needs_targeted_rerun` |
| `needs_deeper_search` | `needs_targeted_rerun` | `needs_targeted_rerun` | `needs_targeted_rerun` |
| `needs_query_reformulation` | `needs_targeted_rerun` | `needs_targeted_rerun` | `needs_targeted_rerun` |
| `source_contradiction` | `needs_verification` | `needs_verification` or `needs_targeted_rerun` | `needs_targeted_rerun` |
| high-confidence `likely_not_publicly_available` | `finalize_with_limitation` | `finalize_with_limitation` | `human_review` |
| low-confidence `likely_not_publicly_available` | one verification | one targeted rerun | `human_review` |
| `criterion_too_ambiguous` | `needs_item_revision` | `human_review` | `human_review` |
| `requires_human_judgment` | `human_review` | `human_review` | `human_review` |

## Rerun Output Contract

Targeted reruns must not return a full merged report. Valid output is limited to:

- `target_item_id`
- `gap_closure_summary`
- `new_evidence_summary`
- `revised_section_delta`
- `remaining_uncertainty`
- `suggested_status_after_rerun`

Application code converts accepted targeted-rerun deltas into deterministic merge
operations. The existing report body is treated as preserved; a targeted rerun
that returns what looks like a full merged report routes to human review.

## No-Progress Handling

No-progress is item based:

- `unanswered` or `partial` to `answered` is progress.
- `unanswered` or `partial` to `unverifiable` is progress.
- Failure-mode confidence gain of at least 10 points is progress.
- A status regression increments no-progress even if another signal improved.
- Same item status and same failure mode without progress increments
  no-progress.
- If either review does not contain item assessments, the fallback heuristic uses
  repeated verdicts, small score deltas, similar gaps or concerns, and unchanged
  report hash.

An `unverifiable` blocker does not pass automatically. It routes to human review
or limitation approval.

## Human Review

Human review payloads include:

- latest report
- latest `ReviewRecord`
- unresolved item list
- failure mode and confidence for each unresolved item
- attempted verification or rerun actions
- audit summary
- allowed actions

Allowed actions:

- `approve`
- `approve_with_limitation`
- `request_review` (allowed only after reviewer execution/schema failures)
- `request_llm_patch`
- `request_verification`
- `request_targeted_rerun`
- `request_item_revision` (returns to human review for manual item revision)
- `reject`

The legacy `request_deep_research` action is invalid in v2.

## Artifacts

Artifacts are written under `RESEARCH_ARTIFACT_DIR`:

- `prompts/optimized_prompt.txt`
- `prompts/rerun_prompt_NNN.txt`
- `raw-responses/*.json`
- `reports/report_attempt_NNN.md`
- `reports/llm_patch_NNN.md`
- `reports/final_report.md`

SQLite state, including objective contracts, research items, rerun plans, and
verification query decisions, checkpoints, and run lineage is stored at
`RESEARCH_DB_PATH`.
