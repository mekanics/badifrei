.PHONY: test test-all test-integration lint format run-collector run-api train

PYTHON := .venv/bin/python
PYTEST  := .venv/bin/pytest
RUFF    := .venv/bin/ruff
BLACK   := .venv/bin/black

test:
	$(PYTEST) tests/unit -v

test-all:
	$(PYTEST) tests/ -v

test-integration:
	$(PYTEST) tests/integration -v -m integration

lint:
	$(RUFF) check . && $(BLACK) --check .

format:
	$(BLACK) . && $(RUFF) check --fix .

run-collector:
	$(PYTHON) -m collector.main

run-api:
	$(PYTHON) -m uvicorn api.main:app --reload

train:
	$(PYTHON) scripts/train.py
