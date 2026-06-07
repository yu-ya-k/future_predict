# Configuration

Runtime settings are loaded from `.env` through Pydantic settings. Tracked
defaults live in `.env.example`; machine-specific values belong in `.env`.

Do not commit secrets. Values prefixed with `VITE_` are exposed to browser code.

## General

| Variable | Default | Used for |
| --- | --- | --- |
| `APP_ENV` | `development` | API runtime environment label returned by `/health`. |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Browser-visible API base URL for the web app. |
| `CORS_ORIGINS` | Local Vite origins on ports `5173`-`5175` | JSON array of browser origins allowed by the API CORS middleware. |
| `RESEARCH_API_KEY` | empty | Optional API key for `/research-runs` endpoints. Empty disables API key authentication for local development. |

When `RESEARCH_API_KEY` is set, every `/research-runs` request must include
either `X-API-Key: <key>` or `Authorization: Bearer <key>`. `/health` remains
unauthenticated.

The web app does not read this key from a `VITE_*` environment variable because
those values are exposed in browser bundles. For browser access, open Settings
and save the Research API key there; the app stores it in that browser's local
storage and sends it as `X-API-Key` on API requests.

## Research Storage

| Variable | Default | Used for |
| --- | --- | --- |
| `RESEARCH_DB_PATH` | `.data/research.sqlite3` | SQLite database for runs, objective contracts, research items, rerun plans, verification queries, checkpoints, run lineage, reviews, citations, tool calls, cost events, human decisions, and history. |
| `RESEARCH_ARTIFACT_DIR` | `.data/research-runs` | Local artifact root for prompts, raw Responses API JSON, report markdown files, and child-local source snapshots for checkpoint forks. |

The repository and artifact store create parent directories as needed.

## Poller And Timeout

| Variable | Default | Used for |
| --- | --- | --- |
| `RESEARCH_POLLER_ENABLED` | `true` | Starts `ResearchPoller` during FastAPI lifespan. |
| `RESEARCH_POLLER_INTERVAL_SECONDS` | `5` | Delay between poller ticks. |
| `RESEARCH_DEEP_RESEARCH_TIMEOUT_SECONDS` | `7200` | Marks waiting Deep Research runs as timed out after this many seconds. |
| `RESEARCH_DEEP_RESEARCH_COLLECTING_STALE_SECONDS` | `60` | Requeues a stale local `collecting` claim after this many seconds so polling can recover after a worker interruption. |
| `RESEARCH_REVIEW_TIMEOUT_SECONDS` | `180` | Bounds GPT-5.5 review calls and marks stale `reviewing` runs as `review_timeout`. |
| `RESEARCH_REVIEW_MAX_REPORT_CHARS` | `50000` | Caps report text sent to the reviewer. Long reports are truncated with a marker. |
| `RESEARCH_REVIEW_MAX_CITATIONS` | `40` | Caps citation metadata sent to the reviewer. |
| `RESEARCH_REVIEW_WEB_SEARCH_ENABLED` | `false` | Enables Web Search tools for structured review calls. LLM patch/finalization fallback and targeted verification have separate public-context/query-policy checks before using Web Search. |

When the poller is disabled, submitted runs can remain `waiting_deep_research`
until code explicitly calls `collect_deep_research`.

Poller intervals, timeout values, and stale-claim windows must be greater than
zero. Report character limits must be at least `1`; citation limits can be `0`
or higher.

## Default Guard Limits

These defaults are copied into each run unless the create request supplies an
override in `options`.

| Variable | Default | Used for |
| --- | --- | --- |
| `DEFAULT_MAX_TARGETED_RERUN_RUNS` | `2` | Maximum targeted Deep Research reruns for a run. |
| `DEFAULT_MAX_FULL_RERUN_RUNS` | `1` | Maximum full Deep Research reruns for a run. |
| `DEFAULT_MAX_LLM_PATCH_RUNS` | `3` | Maximum reviewer LLM patch attempts. |
| `DEFAULT_MAX_VERIFICATION_RUNS` | `3` | Maximum targeted verification attempts. |
| `DEFAULT_MAX_TOTAL_ITERATIONS` | `5` | Maximum total review loop iterations. |
| `DEFAULT_MAX_TOTAL_TOOL_CALLS` | `120` | Tool-call ceiling before human review or resume block. |

Rerun, patch, and verification defaults can be `0` to disable that route.
Total iterations and total tool calls must be at least `1`.

## Cost Estimation

| Variable | Default | Used for |
| --- | --- | --- |
| `RESEARCH_DEEP_RESEARCH_INPUT_COST_PER_1M` | `10` | `o3-deep-research` input token cost per one million tokens. |
| `RESEARCH_DEEP_RESEARCH_OUTPUT_COST_PER_1M` | `40` | `o3-deep-research` output token cost per one million tokens. |
| `RESEARCH_REVIEWER_INPUT_COST_PER_1M` | `5` | `gpt-5.5` reviewer/finalizer input token cost per one million tokens. |
| `RESEARCH_REVIEWER_OUTPUT_COST_PER_1M` | `30` | `gpt-5.5` reviewer/finalizer output token cost per one million tokens. |
| `RESEARCH_WEB_SEARCH_COST_PER_CALL` | `0.01` | Cost added per billable web-search tool call. |

Cost events are estimates based on response usage metadata and extracted tool
calls. When explicit rate environment variables are zero or omitted, known Azure
deployment names are matched back to `o3-deep-research` or `gpt-5.5` and priced
with the built-in defaults above. Unknown models remain at zero unless explicit
rates are configured.

## Azure OpenAI: Deep Research

| Variable | Used for |
| --- | --- |
| `O3_DEEP_RESEARCH_AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint for Deep Research. |
| `O3_DEEP_RESEARCH_AZURE_OPENAI_KEY` | API key for the Deep Research client. |
| `O3_DEEP_RESEARCH_AZURE_OPENAI_API_VERSION` | Azure API version unless the endpoint ends with `/openai/v1`. |
| `O3_DEEP_RESEARCH_AZURE_OPENAI_DEPLOYMENT_NAME` | Deployment name used as the Responses API `model`; default in code is `o3-deep-research`. |

`.env.example` leaves the deployment name empty. After copying it to `.env`, an
empty value overrides the code default, so set this explicitly for real Deep
Research usage.

Public Deep Research submissions call `responses.create` with
`background=True`, web-search tooling, and a bounded `max_tool_calls`.
`internal`, `confidential`, and `mixed` runs are stopped by policy before public
web submission.

## Azure OpenAI: Reviewer And Finalizer

| Variable | Used for |
| --- | --- |
| `GPT5_5_AZURE_OPENAI_ENDPOINT` | Optional separate reviewer/finalizer endpoint. |
| `GPT5_5_AZURE_OPENAI_KEY` | API key for the reviewer/finalizer client. |
| `GPT5_5_AZURE_OPENAI_API_VERSION` | Azure API version unless the endpoint ends with `/openai/v1`. |
| `GPT5_5_AZURE_OPENAI_DEPLOYMENT_NAME` | Deployment name used for structured review and LLM finalization; default in code is `gpt-5.5`. |

`.env.example` leaves the deployment name empty. After copying it to `.env`, an
empty value overrides the code default, so set this explicitly when using the
reviewer/finalizer deployment.

If no `GPT5_5_*` client settings are supplied, the reviewer client falls back to
the Deep Research Azure client while still using
`GPT5_5_AZURE_OPENAI_DEPLOYMENT_NAME` as the model/deployment name. Partial
reviewer settings raise an incomplete-settings error.

## Reserved Candidate Variables

`.env.example` includes:

- `GPT5_4_MINI_AZURE_OPENAI_ENDPOINT`
- `GPT5_4_MINI_AZURE_OPENAI_KEY`
- `GPT5_4_MINI_AZURE_OPENAI_API_VERSION`
- `GPT5_4_MINI_AZURE_OPENAI_DEPLOYMENT_NAME`

Current application settings do not define or read these variables. They are
reserved candidate values and are ignored by current runtime code.

## Live API Test Gate

| Variable | Default | Used for |
| --- | --- | --- |
| `RESEARCH_LIVE_API_TESTS` | `0` | Test fixture gate for real Azure OpenAI / OpenAI calls. Set to `1` only when intentionally running live tests. |
