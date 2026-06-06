# Operations

This guide covers common Research Orchestrator checks while the API is running.

## Run Is Not Completing

1. Check status:

   ```sh
   curl -sS http://127.0.0.1:8000/research-runs/{run_id}
   ```

2. If status is `waiting_deep_research`, confirm the poller is enabled:

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

The payload includes the latest report, latest review, allowed actions, warnings,
and audit summary.

## Resume Decisions

Approve the current report:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"approve","comment":"Approved."}'
```

Ask for an LLM-only fix:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"request_llm_fix","comment":"Address the listed gaps."}'
```

Retry the GPT-5.5 review after a review timeout or malformed review response:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"request_review","comment":"Retry the review."}'
```

Ask for another Deep Research run:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"request_deep_research","comment":"Find stronger current sources."}'
```

Reject the run:

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -d '{"action":"reject","comment":"Not usable for this request."}'
```

If a continuing action returns `409`, inspect the response detail and audit
history. Hard stops include tool-call, total-iteration, no-progress, Deep
Research run, and LLM fix limits. Cost is recorded for visibility, but it does
not block continuation.

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
first attempts the same best-effort remote cancellation as `/cancel`.

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

Artifacts include prompts, rerun briefs, raw response JSON, report attempts,
LLM fixes, and final reports.

## Failure Checklist

- Confirm Azure OpenAI endpoint, key, API version, and deployment names in
  `.env`.
- Confirm `RESEARCH_POLLER_ENABLED=true` for normal API operation.
- Confirm live service credentials separately with the opt-in live tests before
  relying on them operationally.
- Review `/audit` history before inspecting SQLite directly.
- Check artifact files when raw Responses API payloads or saved prompts are
  needed for debugging.
