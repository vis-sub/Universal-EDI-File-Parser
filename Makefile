# Common tasks. Run `make help` for the list.
.DEFAULT_GOAL := help
PORT ?= 8080
N ?= 4
OVERLAY ?= production
VENV := .venv
PY := $(VENV)/bin/python

.PHONY: help up down restart scale ps logs smoke wait dev test lint fuzz serve k8s-render k8s-validate k8s-local samples clean

help:  ## Show this help
	@grep -hE '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

# --- Run the service (Docker) ---------------------------------------------------------
up:  ## Build and start nginx + 2 parser instances on http://localhost:$(PORT)
	EDIPARSE_HOST_PORT=$(PORT) docker compose up --build -d
	@$(MAKE) --no-print-directory wait
	@echo "Service ready: http://localhost:$(PORT)   API docs: http://localhost:$(PORT)/docs"

down:  ## Stop and remove the containers
	docker compose down

restart: down up  ## Rebuild and restart

scale:  ## Run N parser instances (make scale N=4)
	EDIPARSE_HOST_PORT=$(PORT) docker compose up -d --no-recreate --scale ediparse=$(N)
	@sleep 11  # nginx re-resolves instances every 10s
	@docker compose ps

ps:  ## Show containers and health
	docker compose ps

logs:  ## Follow service logs
	docker compose logs -f

wait:
	@for i in $$(seq 60); do curl -fs localhost:$(PORT)/healthz >/dev/null 2>&1 && exit 0; sleep 1; done; \
	  echo "service did not become healthy"; docker compose logs --tail 30; exit 1

smoke:  ## End-to-end checks against the running service
	scripts/smoke-test.sh http://localhost:$(PORT)

# --- Develop without Docker -------------------------------------------------------------
dev:  ## Create .venv with the library, service and test dependencies
	python3 -m venv $(VENV) && $(VENV)/bin/pip install -q --upgrade pip && $(VENV)/bin/pip install -q -e ".[dev]"

test:  ## Run the test suite
	$(VENV)/bin/pytest -q

lint:  ## Lint the Python code
	$(VENV)/bin/ruff check src tests examples tools scripts

fuzz:  ## Long mutation-fuzz run (200,000 cases, ~4 min)
	$(PY) scripts/fuzz.py 10000 20

serve:  ## Run the service from source on http://localhost:$(PORT) (auto-reload off)
	$(VENV)/bin/ediparse serve --port $(PORT)

samples:  ## Regenerate the synthetic X12 sample files
	$(PY) tools/make_samples.py

# --- Kubernetes --------------------------------------------------------------------------
k8s-render:  ## Print the manifests for an overlay (make k8s-render OVERLAY=local)
	kubectl kustomize deploy/kubernetes/overlays/$(OVERLAY)

k8s-validate:  ## Validate both overlays against Kubernetes API schemas (uses Docker)
	for o in local production; do kubectl kustomize deploy/kubernetes/overlays/$$o > /tmp/ediparse-k8s-$$o.yaml; done
	docker run --rm -v /tmp:/t:ro -v "$(CURDIR)/deploy/kubernetes/base:/k:ro" ghcr.io/yannh/kubeconform:latest \
	  -strict -summary /t/ediparse-k8s-local.yaml /t/ediparse-k8s-production.yaml /k/ingress.example.yaml

k8s-local:  ## Build the image and deploy to the current kubectl context with the local overlay
	docker build -t universal-edi-parser:local .
	kubectl create namespace edi --dry-run=client -o yaml | kubectl apply -f -
	kubectl apply -k deploy/kubernetes/overlays/local -n edi
	kubectl -n edi rollout status deploy/ediparse --timeout=180s
	@echo "Try it: kubectl -n edi port-forward svc/ediparse 8080:80"

clean:  ## Remove caches and build output
	rm -rf .pytest_cache build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -not -path './$(VENV)/*' -exec rm -rf {} +
