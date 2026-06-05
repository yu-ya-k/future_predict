# Future Predict

Monorepo for the Future Predict API and web app.

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

`make install` uses `UV_CACHE_DIR=.uv-cache` by default so uv keeps its cache inside the repository workspace.

## Development

```sh
make dev
```

This runs the API at `http://127.0.0.1:8000` and the web app at `http://127.0.0.1:5173`.

Run individual apps with:

```sh
make api
make web
```

## Quality Checks

```sh
make lint
make test
make check
```

## Environment

Tracked defaults live in `.env.example`.

- `APP_ENV`: API runtime environment label.
- `VITE_API_BASE_URL`: browser-visible API base URL for the web app.

Do not commit `.env` or secrets.

