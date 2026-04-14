# ──────────────────────────────────────────────────────────────────────────
# AURA Platform — Developer Shortcuts
# ──────────────────────────────────────────────────────────────────────────
.PHONY: help up down build logs test lint fmt clean frontend

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ── Docker ─────────────────────────────────────────────────────────────────

up: ## Start all backend services (dev mode)
	docker compose up -d --build

up-prod: ## Start all services in production mode
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

down: ## Stop all services
	docker compose down

build: ## Build all Docker images
	docker compose build

logs: ## Tail logs for all services
	docker compose logs -f --tail=50

logs-%: ## Tail logs for a specific service (e.g., make logs-api_gateway)
	docker compose logs -f --tail=50 $*

ps: ## Show running containers
	docker compose ps

restart-%: ## Restart a specific service (e.g., make restart-api_gateway)
	docker compose restart $*

# ── Backend ────────────────────────────────────────────────────────────────

test: ## Run backend test suite
	cd aurabackend && python -m pytest tests/ --tb=short -q --ignore=tests/test_operability.py -x

test-v: ## Run backend tests (verbose)
	cd aurabackend && python -m pytest tests/ --tb=long -v --ignore=tests/test_operability.py

test-cov: ## Run tests with coverage report
	cd aurabackend && python -m pytest tests/ --tb=short --ignore=tests/test_operability.py --cov=. --cov-report=term-missing

lint: ## Run ruff linter on backend
	cd aurabackend && python -m ruff check . --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823

lint-fix: ## Auto-fix ruff lint errors
	cd aurabackend && python -m ruff check . --fix --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823

fmt: ## Format backend code with ruff
	cd aurabackend && python -m ruff format .

# ── Frontend ───────────────────────────────────────────────────────────────

frontend: ## Start frontend dev server
	cd frontend && npm run dev

frontend-build: ## Build frontend for production
	cd frontend && npm run build

frontend-lint: ## Lint frontend TypeScript
	cd frontend && npx eslint src --ext .ts,.tsx --max-warnings 0

frontend-typecheck: ## Run TypeScript type checker
	cd frontend && npx tsc --noEmit

# ── Utilities ──────────────────────────────────────────────────────────────

clean: ## Remove build artifacts, caches, and temp files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf frontend/dist frontend/node_modules/.cache

health: ## Check health of all running services
	@for port in 8000 8001 8002 8003 8004 8005 8006 8007 8009; do \
		printf "  :%-5s " $$port; \
		curl -sf http://localhost:$$port/health | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('service','?'), '—', d.get('status','?'))" 2>/dev/null || echo "DOWN"; \
	done
