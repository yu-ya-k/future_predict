# Repository Instructions

This repository is a monorepo with a FastAPI API in `apps/api` and a Vite
React app in `apps/web`.

## Project Snapshot

- API source lives in `apps/api/src/api`.
- API tests live in `apps/api/tests`.
- Web source lives in `apps/web/src`.
- Human-facing docs live in `README.md` and `docs/`.
- Agent-facing instructions live in this file. Keep them concise and
  repository-specific.

## Operating Rules

- Read the relevant code before editing. Prefer `rg` and `rg --files` for
  searches.
- Keep changes scoped to the user request. Do not refactor unrelated code while
  fixing a local issue.
- Do not revert, delete, or overwrite user changes. If the worktree is dirty,
  preserve unrelated changes and stage only files that belong to the task.
- Use `apply_patch` for manual edits. Formatting commands are acceptable when
  they are part of the requested change.
- Do not commit secrets, local databases, build output, caches, or dependency
  directories.
- If a behavior change touches API contracts, persistence, orchestration, or UI
  workflow, add or update focused tests.

## Environment

- Keep shared local defaults in `.env.example`.
- Keep machine-specific values in `.env`; it is ignored by Git.
- `.envrc` is tracked and loads `.env` for direnv.
- Do not commit secrets. Values prefixed with `VITE_` are exposed to browser
  code.
- Local runtime data is stored under `.data/` by default and should not be
  committed.

## Commands

Prefer root Make targets when they match the task:

```sh
make install
make dev
make check
```

- `make install` creates `.env` from `.env.example` when missing and installs
  API and web dependencies.
- `make dev` runs the API on `127.0.0.1:8000` and the web app on
  `127.0.0.1:5173`.
- `make check` runs API Ruff, API Pyright, API pytest, and the web check target.

## Python API

- Use Python 3.12.
- Use uv for dependency management.
- Keep uv cache local when running commands in this workspace:

```sh
UV_CACHE_DIR=.uv-cache uv --project apps/api run pytest apps/api/tests
```

- Run API checks with:

```sh
UV_CACHE_DIR=.uv-cache uv --project apps/api run ruff check apps/api/src apps/api/tests
UV_CACHE_DIR=.uv-cache uv --project apps/api run pyright -p apps/api
UV_CACHE_DIR=.uv-cache uv --project apps/api run pytest apps/api/tests
```

- The default pytest configuration excludes live API tests with the `live_api`
  marker.
- Use FastAPI and Pydantic patterns already present under `apps/api/src/api`.

## Web

- Use Corepack-managed pnpm.
- Keep Corepack cache local when running commands in this workspace:

```sh
COREPACK_HOME=$PWD/.corepack corepack pnpm --dir apps/web check
```

- Do not embed secrets in frontend code. Anything exposed as `VITE_*` is
  browser-visible.
- Run web checks with:

```sh
COREPACK_HOME=$PWD/.corepack corepack pnpm --dir apps/web check
```

- Follow existing React, TypeScript, Vite, ESLint, and Vitest patterns in
  `apps/web`.

## Research Orchestrator

- The API includes a Deep Research Review Orchestrator with background
  submission, polling, collection, review, bounded repair loops, human review,
  audit records, and local artifacts.
- Keep detailed user/operator information in these docs instead of duplicating
  it here:
  - `docs/research-orchestrator.md`
  - `docs/api.md`
  - `docs/configuration.md`
  - `docs/testing.md`
  - `docs/operations.md`
- Human-review resume decisions currently do not require reviewer identity.
- Live Azure OpenAI / OpenAI tests are opt-in only. Do not run them unless the
  user explicitly asks for live API testing and the required environment is set.

## Testing

- For docs-only changes, run:

```sh
git diff --check
```

- For executable changes, run the narrowest relevant checks first, then
  `make check` when the change can affect shared behavior.
- Local integration tests can be run with:

```sh
UV_CACHE_DIR=.uv-cache uv --project apps/api run pytest apps/api/tests/integration -m integration
```

- Live API tests may incur cost and require explicit opt-in:

```sh
RESEARCH_LIVE_API_TESTS=1 UV_CACHE_DIR=.uv-cache uv --project apps/api run pytest apps/api/tests/live -m live_api
```

## Commit / Review Expectations

- Before committing, inspect `git status --short` and stage only intended files.
- Prefer small commits that match the completed task.
- When reviewing, lead with concrete bugs, regressions, missing tests, or
  security risks. Cite file paths and line numbers.
