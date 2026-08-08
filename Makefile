.PHONY: test test-all test-integration lint format run-collector run-api train

PYTHON := .venv/bin/python
PYTEST  := .venv/bin/pytest
RUFF    := .venv/bin/ruff

test:
	$(PYTEST) tests/unit -v

test-all:
	$(PYTEST) tests/ -v

test-integration:
	$(PYTEST) tests/integration -v -m integration

lint:
	$(RUFF) check . && $(RUFF) format --check .

format:
	$(RUFF) format . && $(RUFF) check --fix .

run-collector:
	$(PYTHON) -m collector.main

run-api:
	$(PYTHON) -m uvicorn api.main:app --reload

train:
	DATABASE_URL=$${DATABASE_URL:-postgresql://badi:badi@localhost:5432/badi} $(PYTHON) scripts/train.py
