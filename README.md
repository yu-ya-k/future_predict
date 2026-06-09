# Future Predict

Future Predict is a monorepo for a FastAPI service and a Vite React app.

The API includes a Deep Research Review Orchestrator that submits background
Deep Research jobs, polls for completion, reviews generated reports, loops
through bounded fixes when useful, and escalates uncertain or blocked runs to
human review.

It also includes Forecast PhaseA: a public-only forecast workflow that frames a
question, dispatches one `current_state` Deep Research pack, extracts sourced
evidence, generates outcome-bound scenarios, computes reproducible `phase_a_v1`
probabilities, and freezes approved versions for audit.

## Stack

- API: FastAPI, Python 3.12, uv
- Web: Vite, React, TypeScript, pnpm through Corepack
- Environment: root `.env` loaded by direnv through `.envrc`

## Setup

```sh
cp .env.example .env
direnv allow
make install
```

`make install` uses `UV_CACHE_DIR=.uv-cache` and `COREPACK_HOME=.corepack`
by default so local dependency caches stay inside the repository workspace.

## Development

```sh
make dev
```

This runs the API at `http://127.0.0.1:8000` and the web app at
`http://127.0.0.1:5173`.

Run individual apps with:

```sh
make api
make web
```

## Quality Checks

```sh
make check
```

`make check` runs API Ruff, API Pyright, API pytest, and the web check target.
The default pytest configuration excludes live API tests.

## Research Orchestrator

Start here for the implemented research workflow:

- [Research Orchestrator](docs/research-orchestrator.md)
- [API](docs/api.md)
- [Configuration](docs/configuration.md)
- [Testing](docs/testing.md)
- [Operations](docs/operations.md)

Forecast PhaseA uses the same SQLite database as Research. Forecast-linked
ResearchRuns cannot be deleted while referenced by a forecast pack.

Live Azure OpenAI / OpenAI API tests are opt-in because they call real services
and may incur cost. See [Testing](docs/testing.md) for the explicit command and
required environment variables.

## Environment

Tracked defaults live in `.env.example`.

Machine-specific values belong in `.env`, which is ignored by Git. Do not commit
secrets. Values prefixed with `VITE_` are exposed to browser code.
