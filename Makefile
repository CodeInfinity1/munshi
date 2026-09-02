# Munshi. `make demo` is the only command a reviewer needs.
.DEFAULT_GOAL := help
PY ?= python3
PORT ?= 8000

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install Python and frontend dependencies
	$(PY) -m pip install -e ".[dev]"
	cd web && npm ci

build: ## Build the dashboard into the Python package
	cd web && npm run build

seed: ## Generate and load the 320-case demo batch
	$(PY) -m munshi.seed.load

eval: ## Run the batch evaluation across all arms and write evaluation/
	$(PY) -m munshi.evaluation.harness \
	  --arms baseline,agent-heuristic,agent-heuristic-approved

test: ## Run the test suite
	$(PY) -m pytest -q

lint: ## Ruff + TypeScript
	$(PY) -m ruff check munshi tests
	cd web && npm run typecheck

serve: ## Start the API and dashboard on $(PORT)
	$(PY) -m uvicorn munshi.api:app --host 127.0.0.1 --port $(PORT)

demo: build seed serve ## Build, seed and serve: the whole demo in one command

check: lint test eval ## Everything CI runs

clean:
	rm -f munshi.db munshi.db-wal munshi.db-shm
	rm -rf munshi/static web/dist .pytest_cache .ruff_cache

.PHONY: help install build seed eval test lint serve demo check clean
