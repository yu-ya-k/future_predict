UV_CACHE_DIR ?= .uv-cache
COREPACK_HOME ?= $(CURDIR)/.corepack
UV := UV_CACHE_DIR=$(UV_CACHE_DIR) uv
PNPM ?= COREPACK_HOME=$(COREPACK_HOME) corepack pnpm
API_DIR := apps/api
WEB_DIR := apps/web

.PHONY: install dev api web lint format test check clean

install:
	test -f .env || cp .env.example .env
	$(UV) --project $(API_DIR) sync --all-groups
	$(PNPM) install

dev:
	$(MAKE) -j2 api web

api:
	$(UV) --project $(API_DIR) run uvicorn api.main:app --reload --host 127.0.0.1 --port 8000

web:
	$(PNPM) --dir $(WEB_DIR) dev --host 127.0.0.1 --port 5173

lint:
	$(UV) --project $(API_DIR) run ruff check $(API_DIR)/src $(API_DIR)/tests
	$(PNPM) --dir $(WEB_DIR) lint

format:
	$(UV) --project $(API_DIR) run ruff format $(API_DIR)/src $(API_DIR)/tests
	$(PNPM) --dir $(WEB_DIR) format

test:
	$(UV) --project $(API_DIR) run pytest $(API_DIR)/tests
	$(PNPM) --dir $(WEB_DIR) test

check:
	$(UV) --project $(API_DIR) run ruff check $(API_DIR)/src $(API_DIR)/tests
	$(UV) --project $(API_DIR) run pyright -p $(API_DIR)
	$(UV) --project $(API_DIR) run pytest $(API_DIR)/tests
	$(PNPM) --dir $(WEB_DIR) check

clean:
	rm -rf $(API_DIR)/.venv $(WEB_DIR)/node_modules $(WEB_DIR)/dist $(WEB_DIR)/coverage .pytest_cache .ruff_cache .uv-cache
