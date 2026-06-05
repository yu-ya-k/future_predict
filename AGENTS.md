# Repository Instructions

This repository is a monorepo with a FastAPI app in `apps/api` and a Vite React app in `apps/web`.

## Environment

- Keep shared local defaults in `.env.example`.
- Keep machine-specific values in `.env`; it is ignored by Git.
- `.envrc` is tracked and loads `.env` for direnv.
- Do not commit secrets. Values prefixed with `VITE_` are exposed to browser code.

## Python API

- Use Python 3.12.
- Use uv for dependency management.
- Keep uv cache local when running commands in this workspace:

```sh
UV_CACHE_DIR=.uv-cache uv --project apps/api run pytest
```

- API source lives under `apps/api/src/api`.
- Tests live under `apps/api/tests`.
- Run API checks with:

```sh
UV_CACHE_DIR=.uv-cache uv --project apps/api run ruff check apps/api/src apps/api/tests
UV_CACHE_DIR=.uv-cache uv --project apps/api run pyright -p apps/api
UV_CACHE_DIR=.uv-cache uv --project apps/api run pytest apps/api/tests
```

## Web

- Use Corepack-managed pnpm.
- Keep Corepack cache local when running commands in this workspace:

```sh
COREPACK_HOME=$PWD/.corepack corepack pnpm --dir apps/web check
```

- Web source lives under `apps/web/src`.
- Do not embed secrets in frontend code.
- Run web checks with:

```sh
COREPACK_HOME=$PWD/.corepack corepack pnpm --dir apps/web check
```

## Common Commands

Prefer root Make targets:

```sh
make install
make dev
make check
```
