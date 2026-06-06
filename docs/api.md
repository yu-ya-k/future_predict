# API

The research API is mounted under `/research-runs`. Examples assume the API is
running at `http://127.0.0.1:8000`.

## Create A Run

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs \
  -H 'Content-Type: application/json' \
  -d '{
    "user_prompt": "Research the market outlook for battery recycling in Japan.",
    "options": {
      "max_deep_research_runs": 2,
      "max_llm_fix_runs": 3,
      "max_total_iterations": 5,
      "max_no_progress_rounds": 2,
      "max_cost_usd": 20,
      "max_total_tool_calls": 120
    }
  }'
```

Response status is `202 Accepted`:

```json
{
  "run_id": "00000000-0000-0000-0000-000000000000",
  "thread_id": "11111111-1111-1111-1111-111111111111",
  "status": "waiting_deep_research",
  "created_at": "2026-01-01T00:00:00Z"
}
```

`options` is optional. Defaults come from the API settings. The MVP product
supports public Web Research only; Web Search is always enabled for research,
review, and finalization. The create API does not accept source-category or
search-toggle fields such as `context_classification` or `allow_web_search`.

## Status And Progress

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}
```

The response includes `status`, `done_reason`, `needs_human_review`, and
progress counters:

```json
{
  "run_id": "00000000-0000-0000-0000-000000000000",
  "status": "completed",
  "done_reason": "passed_review",
  "needs_human_review": false,
  "progress": {
    "deep_research_runs": 1,
    "llm_fix_runs": 0,
    "total_reviews": 1,
    "latest_verdict": "pass",
    "latest_score": 95,
    "total_tool_calls": 3,
    "estimated_cost_usd": 0.42
  }
}
```

## Report

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/report
```

`final_report` is populated when the run is completed. `report` contains the
latest candidate report even when the run still needs review.

## Audit

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/audit
```

The audit response contains:

- `attempts`
- `reviews`
- `citations`
- `tool_calls`
- `cost_events`
- `human_decisions`
- `history`

## Human Review Authentication

Human-review endpoints require the `X-Reviewer-Id` header. The reviewer id is
recorded with the decision. Supplying `reviewer_id` in the JSON body is rejected
by the resume API.

Missing `X-Reviewer-Id` returns `401`.

## Human Review Queue

```sh
curl -sS http://127.0.0.1:8000/research-runs/human-reviews \
  -H 'X-Reviewer-Id: reviewer-1'
```

Each queue item includes the run id, latest verdict and score, rationale,
created/updated timestamps, and an audit summary.

## Human Review Payload

```sh
curl -sS http://127.0.0.1:8000/research-runs/{run_id}/human-review \
  -H 'X-Reviewer-Id: reviewer-1'
```

The payload includes the latest report, latest review, audit summary, warnings,
reason, and allowed actions:

- `approve`
- `request_llm_fix`
- `request_deep_research`
- `reject`

If the run is not waiting for human review, the endpoint returns `409`.

## Resume From Human Review

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/resume \
  -H 'Content-Type: application/json' \
  -H 'X-Reviewer-Id: reviewer-1' \
  -d '{
    "action": "approve",
    "comment": "Reviewed and approved."
  }'
```

Actions:

- `approve`: finalize the latest report and complete the run with
  `done_reason=human_approved`.
- `request_llm_fix`: run the reviewer deployment as an editor, then review the
  revised report.
- `request_deep_research`: submit another Deep Research run and wait for the
  poller to collect it.
- `reject`: fail the run with `done_reason=human_rejected` and append the
  reviewer comment to warnings.

Continuing actions can return `409` when a guard has already been reached.
Only one resume decision can claim a waiting human-review run.

## Cancel

```sh
curl -sS -X POST http://127.0.0.1:8000/research-runs/{run_id}/cancel
```

If the run is waiting on a remote Deep Research response, the orchestrator tries
to cancel that response. The local run is marked `cancelled` with
`done_reason=cancelled_by_user`.

## Diagnostic Endpoints

The audit endpoint is the broadest diagnostic API. Narrow endpoints also exist:

- `GET /research-runs/{run_id}/citations`
- `GET /research-runs/{run_id}/reviews`
- `GET /research-runs/{run_id}/attempts`
- `GET /research-runs/{run_id}/tool-calls`
- `GET /research-runs/{run_id}/cost-events`
- `GET /research-runs/{run_id}/human-decisions`

`human-decisions` also requires `X-Reviewer-Id`.

## Public Interface Summary

- `POST /research-runs`
- `GET /research-runs/{run_id}`
- `GET /research-runs/{run_id}/report`
- `GET /research-runs/{run_id}/audit`
- `GET /research-runs/human-reviews`
- `GET /research-runs/{run_id}/human-review`
- `POST /research-runs/{run_id}/resume`
- `POST /research-runs/{run_id}/cancel`
