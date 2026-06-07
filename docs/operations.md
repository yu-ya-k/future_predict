# Operations

This guide covers common Research Orchestrator checks while the API is running.

## Run Is Not Completing

1. Check status:

   ```sh
   curl -sS http://127.0.0.1:8000/research-runs/{run_id}
   ```

2. If status is `waiting_deep_research`, or a manual import is stuck in
   `reviewing` before any `review_attempt_started` audit entry, confirm the
   poller is enabled:

   ```sh
   grep '^RESEARCH_POLLER_ENABLED=' .env
   ```

3. Check the timeout and poll interval:

   ```sh
   grep '^RESEARCH_DEEP_RESEARCH_TIMEOUT_SECONDS=' .env
   grep '^RESEARCH_REVIEW_TIMEOUT_SECONDS=' .env
   grep '^RESEARCH_POLLER_INTERVAL_SECONDS=' .env
   ```

4. Inspect audit history:

   ```sh
   curl -sS http://127.0.0.1:8000/research-runs/{run_id}/audit
   ```

Look for `deep_research_retrieve_retryable_error`, `deep_research_timeout`,
`deep_research_*` terminal reasons, `review_attempt_started`, `review_timeout`,
`review_failed`, or
`human_review_required`.

## Human Review Queue

List runs waiting for a reviewer:

```sh
curl -sS http://127.0.0.1:8000/research-runs/human-reviews
```

Fetch the payload for one run:

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/human-review
```

The payload includes the latest report, latest review, unresolved ResearchItems,
allowed actions, warnings, and audit summary.

## Manual Import

Import a ChatGPT Deep Research run from local Markdown or text files:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/manual-import \
  -F input_prompt_file=@prompt.md \
  -F report_file=@report.md \
  -F allow_remote_review=true \
  -F allow_api_reruns=true \
  -F rerun_execution_mode=manual_chatgpt \
  -F idempotency_key=operator-ticket-1234
```

Use `allow_remote_review=false` when the report should be archived and queued
for a human before any reviewer/finalizer/verification model call. Sensitive
terms in the imported body do not block remote review when
`allow_remote_review=true`; operators should use `allow_remote_review=false`
when content must stay local. Use `allow_api_reruns=false` or
`rerun_execution_mode=disabled` to prevent future targeted or full reruns from
this imported run; `allow_api_reruns=false` also normalizes an explicit
`rerun_execution_mode=api` to `disabled`. Use
`rerun_execution_mode=manual_chatgpt` to pause future reruns at a ChatGPT
prompt instead of submitting Deep Research through the API.

Keep uploaded files within the server's
`RESEARCH_MANUAL_IMPORT_MAX_FILE_BYTES` limit and report text within
`RESEARCH_MANUAL_IMPORT_MAX_REPORT_CHARS`. The web app may block at its
client-side default before submission, but the API returns `422` for any request
that exceeds the deployed server configuration.

Manual imports appear in audit attempts with `source=manual_upload`. Extracted
URLs are saved as `manual_upload_url_unverified` citations and do not count as
tool calls.

When a manual ChatGPT rerun is pending, fetch the prompt from the human-review
payload, run it in ChatGPT, then upload the result:

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/human-review

curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/manual-rerun-result \
  -F rerun_id=RR-... \
  -F report_file=@rerun-result.md
```

The upload endpoint returns `409` for stale rerun ids or targeted results that
look like full merged reports. The pending prompt remains active after those
conflicts so the operator can retry with the current payload. It also returns
`409` when there is no active pending manual rerun, the run is not a manual
import configured for ChatGPT reruns, the report changed since prompt
generation, or a previously accepted `rerun_id` is resent with different
content. In any conflict case, refetch
`/research-runs/{run_id}/human-review`, keep the operator's local result text,
and inspect `/research-runs/{run_id}/audit` before retrying.

If the rerun prompt itself is blocked by query policy, the human-review payload
does not include `pending_manual_rerun`. The stop reason is
`manual_rerun_prompt_blocked_by_query_policy`; use the audit log to inspect the
blocked plan and decide whether to reject, revise the task, or continue by a
different allowed route.

If the API process exits after the import is saved but before background review
starts, the poller resumes the pending manual review on a later tick. With
`RESEARCH_POLLER_ENABLED=false`, that recovery does not run; operators should
either re-enable the poller or resume/review the run through the normal human
review path after inspecting the audit log.

## Checkpoint Forks

List saved checkpoints for a run:

```sh
curl -sS 'http://127.0.0.1:8000/research-runs/{run_id}/checkpoints?include_forks=true'
```

Fetch the checkpoint detail before deciding to fork:

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/checkpoints/{checkpoint_id}
```

Create a preview. Operators should inspect the composed prompt, source excerpts,
warnings, and query-policy status before submitting:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/checkpoints/{checkpoint_id}/fork-preview \
  -H 'Content-Type: application/json' \
  -d '{"additional_prompt":"Re-check this branch with the latest 2026 sources."}'
```

Submit with the returned `preview_hash` and a stable `idempotency_key`:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/checkpoints/{checkpoint_id}/forks \
  -H 'Content-Type: application/json' \
  -d '{
    "additional_prompt": "Re-check this branch with the latest 2026 sources.",
    "idempotency_key": "operator-ticket-1234",
    "confirmed_preview_hash": "sha256:..."
  }'
```

Forks are cost and execution boundaries. A fork creates an independent child
run; the parent report, audit, counters, cost, and progress are unchanged. The
child starts with zero counters and records only new child execution. If query
policy blocks the fork prompt, no remote Deep Research call is made; the child
is created in human review with
`done_reason=fork_deep_research_blocked_by_query_policy`.

Inspect lineage from the child:

```sh
curl -sS http://127.0.0.1:8000/research-runs/{child_run_id}/lineage
```

## Resume Decisions

Approve the current report:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"approve","comment":"Approved."}'
```

Ask for a bounded LLM patch:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"request_llm_patch","comment":"Address the listed item gaps."}'
```

Retry the GPT-5.5 review after a review timeout or malformed review response:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"request_review","comment":"Retry the review."}'
```

Ask for targeted verification:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"request_verification","comment":"Verify the disputed ResearchItems."}'
```

Ask for a targeted Deep Research rerun:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"request_targeted_rerun","comment":"Find stronger current sources for the unresolved items."}'
```

For a manual import configured with `rerun_execution_mode=manual_chatgpt`, the
same action creates a pending ChatGPT rerun prompt instead of calling the Deep
Research API.

Approve with limitations:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"approve_with_limitation","comment":"Accept with the listed limitations."}'
```

Reject the run:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"reject","comment":"Not usable for this request."}'
```

If a continuing action returns `409`, inspect the response detail and audit
history. Hard stops include tool-call, total-iteration, no-progress, Deep
Research rerun, LLM patch, and verification limits. Cost is recorded for
visibility, but it does not block continuation.

## Cancel

Cancel a run:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/cancel
```

If a remote Deep Research response is pending, the orchestrator attempts to
cancel it. The local run is marked `cancelled` even if remote cancellation
records a `cancel_remote_failed` history event.

## Delete

Delete a run and its local artifacts:

```sh
curl -sS -X DELETE http://127.0.0.1:8000/research-runs/{run_id}
```

Deletion physically removes the run, related audit rows, and
`.data/research-runs/{run_id}`. If the run is still active, the orchestrator
first attempts the same remote cancellation as `/cancel`; if that remote
cancellation fails, deletion returns `409` and the local run is preserved.

## Audit, Citations, Tool Calls, And Cost

Use the aggregate audit endpoint first:

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/audit
```

Use narrow endpoints when you only need one audit class:

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/citations
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/tool-calls
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/cost-events
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/reviews
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/attempts
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/contract
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/items
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/rerun-plans
```

Human decisions can be fetched with:

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/human-decisions
```

## Local Storage

Check current configured storage:

```sh
grep '^RESEARCH_DB_PATH=' .env
grep '^RESEARCH_ARTIFACT_DIR=' .env
```

By default:

- SQLite database: `.data/research.sqlite3`
- Artifacts: `.data/research-runs`

Artifacts include prompts, rerun briefs, manual ChatGPT rerun result uploads,
raw response JSON, report attempts, LLM patches, final reports, and child-local
source snapshots for checkpoint forks. SQLite stores checkpoint rows and
denormalized child lineage so lineage remains available from the child after
parent deletion. Startup migrations keep older local databases compatible:
existing `research_runs` rows default to `rerun_execution_mode=api`, and manual
ChatGPT rerun prompt/upload state is stored in `manual_rerun_requests`. The
table enforces one active pending manual rerun per run.

## Failure Checklist

- Confirm Azure OpenAI endpoint, key, API version, and deployment names in
  `.env`.
- Confirm `RESEARCH_POLLER_ENABLED=true` for normal API operation.
- Confirm live service credentials separately with the opt-in live tests before
  relying on them operationally.
- Review `/audit` history before inspecting SQLite directly.
- For fork issues, compare the latest preview hash with the submit payload and
  check `/lineage` from the child run.
- Check artifact files when raw Responses API payloads or saved prompts are
  needed for debugging.
