# Research Orchestrator

The Research Orchestrator is implemented in `apps/api/src/api/research`. It
coordinates background Deep Research, report review, bounded repair loops,
human review, persistence, audit records, and local artifacts.

## Workflow

1. `POST /research-runs` creates a run, stores it in SQLite, builds an optimized
   research prompt, and submits an Azure OpenAI Responses background request for
   Deep Research.
2. The API stores the run as `waiting_deep_research` with the pending response
   id.
3. The app lifespan starts `ResearchPoller` when `RESEARCH_POLLER_ENABLED=true`.
   Each tick marks timed-out runs or collects waiting Deep Research responses.
4. Collection retrieves the Responses API result. A completed response produces
   a report, citations, tool-call summaries, cost events, raw response artifacts,
   and a review step.
5. Review uses the reviewer deployment with structured output matching
   `ReviewResult`. It records review details, citations, tool calls, cost
   events, and a `route_after_review` history event.
6. Routing can finalize, request an LLM fix, request another Deep Research run,
   or enter human review.
7. LLM fixes revise the current report and immediately re-enter review.
8. Additional Deep Research runs create a rerun brief, submit another background
   request, and wait for the poller.
9. Human review exposes a queue and payload through API endpoints. A reviewer
   resumes the run with one of the allowed actions.

## Phases

- Phase 1-2: strict schemas, review routing, optimized prompts, persistence,
  artifacts, and local test coverage.
- Phase 3: automated collect -> review -> finalize / LLM fix / Deep Research
  loop / human review routing. `build_phase_3_graph` mirrors this flow for
  LangGraph tests.
- Phase 4: human-review interrupt and resume routing. `build_phase_4_graph`
  requires a checkpointer and resumes with a LangGraph `Command`.

The production HTTP path is `ResearchOrchestrator`; the graph module provides
testable workflow shapes for the same route decisions.

## MVP Scope

The MVP treats every run as public Web Research. Web Search is required for
Deep Research and remains available for finalization. GPT-5.5 review uses the
report and collected citations by default, with reviewer Web Search available
only through explicit configuration. The app does not accept or route on source
categories or a per-run search-toggle policy.

## Statuses

Runs use these status values:

- `queued`
- `submitted`
- `waiting_deep_research`
- `collecting`
- `reviewing`
- `needs_action`
- `needs_human_review`
- `completed`
- `cancelled`
- `failed`

Current code most commonly exposes `waiting_deep_research`,
`needs_human_review`, `completed`, `cancelled`, and `failed` through the API.

## Review Verdicts

Reviewer structured output can return:

- `pass`: finalize the current report.
- `needs_llm_fix`: use the reviewer deployment to revise the report when safe.
- `needs_deep_research`: submit another Deep Research run when limits allow.
- `human_review`: stop automation and require a reviewer decision.

## Guard Conditions

Automation routes to human review when continuing would exceed or violate a
guard:

- `max_total_iterations`
- `max_deep_research_runs`
- `max_llm_fix_runs`
- `max_no_progress_rounds`
- `max_total_tool_calls`
- malformed or failed review output
- missing report or missing response id
- Deep Research terminal failure, unknown status, timeout, or submit failure

The same guards also block human resume actions that would continue automated
work (`request_llm_fix` or `request_deep_research`) after a hard stop.
`request_review` retries only the GPT-5.5 review step and is exposed when the
review itself failed, for example `review_timeout` or
`review_schema_or_request_failed`. `approve` and `reject` remain terminal
reviewer actions.

## No-Progress Handling

After each review, the orchestrator compares current and previous review state
using `compute_no_progress_count`. Repeated rounds without meaningful progress
increment `no_progress_count`. When it reaches `max_no_progress_rounds`, the
run enters human review instead of continuing the loop.

## Cost And Tool-Call Tracking

The orchestrator records cost events for Deep Research, review, failed review
responses with billable metadata, and LLM finalization. Estimated cost is derived
from each cost event's model, token usage, and billable web-search calls.
`o3-deep-research` and Azure deployment names containing `o3-deep-research`
use the Deep Research rate, while `gpt-5.5` and deployment names containing
`gpt5.5` use the reviewer/finalizer rate. Tool calls are counted from extracted
response tool-call summaries and stored for audit.

Defaults come from `.env.example` and the limit values can be overridden per
run through `ResearchRunOptions`.

## Artifacts

Artifacts are written under `RESEARCH_ARTIFACT_DIR`:

- `prompts/optimized_prompt.txt`
- `prompts/rerun_prompt_NNN.txt`
- `raw-responses/*.json`
- `reports/report_attempt_NNN.md`
- `reports/llm_fix_NNN.md`
- `reports/final_report.md`

SQLite state is stored at `RESEARCH_DB_PATH`.
