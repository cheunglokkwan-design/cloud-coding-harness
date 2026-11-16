.PHONY: setup image dev api web test lint bench-startup bench-concurrency up down

VENV := orchestrator/.venv
PY := $(VENV)/bin/python

setup:            ## install Python + web dependencies
	python3 -m venv $(VENV)
	$(PY) -m pip install -e 'orchestrator[dev]'
	cd web && npm ci

image:            ## build the prebuilt sandbox image
	docker build -t cch-sandbox:latest sandbox-image

api:              ## run the orchestrator API on :8080 (docker sandboxes)
	$(VENV)/bin/harness serve

api-local:        ## run the API with the no-Docker local driver (dev only, not isolated)
	HARNESS_SANDBOX_DRIVER=local $(VENV)/bin/harness serve

web:              ## run the React dev server on :5173 (proxies /api to :8080)
	cd web && npm run dev

demo:             ## run the scripted demo agent once and stream it to the terminal
	$(VENV)/bin/harness run --repo examples/demo-calc --task "Fix median()" --provider scripted

test:             ## run all tests
	cd orchestrator && .venv/bin/python -m pytest -q
	cd web && npm test

lint:
	cd orchestrator && .venv/bin/ruff check harness tests
	cd web && npm run typecheck

bench-startup:    ## sandbox startup: cold vs cached vs warm
	$(PY) scripts/bench.py startup --n 20 --repo https://github.com/pallets/flask --out docs/benchmarks/startup-flask.json

bench-concurrency: ## 120 concurrent scripted agents
	$(PY) scripts/bench.py concurrency --agents 120 --step-delay-ms 3000 --pool 128 --out docs/benchmarks/concurrency-120-prewarmed.json

up:
	docker compose up --build

down:
	docker compose down
