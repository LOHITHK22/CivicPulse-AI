# CivicPulse AI developer commands. On Windows use Git Bash (or run the commands directly).
PY ?= .venv/bin/python
ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python
endif

.PHONY: setup extract backfill-history incremental build manifest models sample-warehouse api web test test-live lint e2e check

setup:            ## create venv, install Python + web deps
	python -m venv .venv
	$(PY) -m pip install -e ".[dev,ml]"
	cd apps/web && npm ci

extract:          ## bounded backfill of the configured window from the live NYC Open Data API
	$(PY) -m civicpulse_ingestion.cli extract --mode backfill

backfill-history: ## M2: bounded half-year backfills 2023-01 -> 2025-11 (resumable, skips finished chunks)
	PY=$(PY) bash scripts/backfill_history.sh

incremental:      ## incremental update using the stored watermark + lookback
	$(PY) -m civicpulse_ingestion.cli extract --mode incremental

build:            ## rebuild warehouse tables + quality report from data/raw
	$(PY) -m civicpulse_ingestion.cli build

manifest:         ## write data/state/manifest.json (+ docs copy) and re-count every backfill at the source
	$(PY) -m civicpulse_ingestion.cli manifest --verify-source-counts --out docs/data/manifest.json

models:           ## train + evaluate all M2 models from the current warehouse (offline)
	$(PY) -m civicpulse_modeling.cli train

sample-warehouse: ## offline: warehouse + smoke-scale models from the committed real sample into .sample-data
	$(PY) scripts/build_sample_warehouse.py --data-dir .sample-data --with-models

api:              ## run the analytics API on :8000
	$(PY) -m uvicorn civicpulse_api.main:app --host 127.0.0.1 --port 8000

web:              ## run the web app on :3000 (production build)
	cd apps/web && npx next build && npx next start -p 3000

lint:
	$(PY) -m ruff check . && $(PY) -m ruff format --check . && $(PY) -m mypy
	cd apps/web && npx tsc --noEmit && npx eslint .

test:             ## offline Python tests (no network)
	$(PY) -m pytest

test-live:        ## opt-in checks against the real NYC Open Data API
	$(PY) -m pytest -m live

e2e:              ## Playwright against API + web (starts servers if not running)
	cd apps/web && npx playwright test

check: lint test e2e
