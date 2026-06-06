# Testing

Use the root Make targets for normal development checks.

## Full Local Check

```sh
make check
```

`make check` runs:

```sh
UV_CACHE_DIR=.uv-cache uv --project apps/api run ruff check apps/api/src apps/api/tests
UV_CACHE_DIR=.uv-cache uv --project apps/api run pyright -p apps/api
UV_CACHE_DIR=.uv-cache uv --project apps/api run pytest apps/api/tests
COREPACK_HOME=$PWD/.corepack corepack pnpm --dir apps/web check
```

## API Unit And Local Integration Tests

```sh
UV_CACHE_DIR=.uv-cache uv --project apps/api run pytest apps/api/tests
```

The root `pytest.ini` and `apps/api/pyproject.toml` both set default pytest
options that exclude live API tests:

```ini
addopts = -q -m "not live_api"
```

The `integration` marker covers local tests that exercise multiple app layers
with fakes. They do not call real Azure OpenAI services.

Run only local integration tests:

```sh
UV_CACHE_DIR=.uv-cache uv --project apps/api run pytest apps/api/tests/integration -m integration
```

## Live API Tests

Live tests are marked `live_api`, excluded from default pytest, and guarded by
the `RESEARCH_LIVE_API_TESTS` environment variable. They call real Azure OpenAI
or OpenAI-compatible endpoints and may incur cost.

Run them only when intentionally testing live credentials:

```sh
RESEARCH_LIVE_API_TESTS=1 \
UV_CACHE_DIR=.uv-cache uv --project apps/api run pytest apps/api/tests/live -m live_api
```

Required Deep Research settings:

- `O3_DEEP_RESEARCH_AZURE_OPENAI_ENDPOINT`
- `O3_DEEP_RESEARCH_AZURE_OPENAI_KEY`
- `O3_DEEP_RESEARCH_AZURE_OPENAI_DEPLOYMENT_NAME`
- `O3_DEEP_RESEARCH_AZURE_OPENAI_API_VERSION`, unless the endpoint ends with
  `/openai/v1`

Reviewer tests require either a complete `GPT5_5_*` client configuration or a
complete Deep Research client configuration for fallback. `GPT5_5_*` API version
is optional only when the endpoint ends with `/openai/v1`.

The Deep Research live smoke test attempts to cancel any non-terminal background
response it submits.

## Web Checks

```sh
COREPACK_HOME=$PWD/.corepack corepack pnpm --dir apps/web check
```

Use Corepack-managed pnpm and keep the Corepack cache local to the workspace.

## Markdown Sanity

Before committing documentation changes, run:

```sh
git diff --check
```

This catches trailing whitespace and whitespace errors in changed files.
