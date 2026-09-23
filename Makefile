# ============================================================================
# pf-payroll - Chilean payroll microservice
# ============================================================================

# Service configuration (REQUIRED by common.mk)
APP_PORT := 8000
APP_MODULE := payroll.interfaces.api.main:app

# Include shared targets from pf-common/
include ../pf-common/make/common.mk

# ============================================================================
# Service-specific variables
# ============================================================================

DB_CONTAINER ?= pf-db-db-1
PF_DATABASE_URL ?= postgresql+asyncpg://pf_db:pf_db@localhost:5432/pf_db
PF_RATES_URL ?= http://localhost:8001
PF_PAYROLL_API_KEY ?= change-me-before-use

# ============================================================================
# Service-specific targets
# ============================================================================

.PHONY: env-write
env-write: ## Write .env file with service-specific defaults (delegates to scripts/write_env.sh)
	@PF_DATABASE_URL="$(PF_DATABASE_URL)" \
		PF_RATES_URL="$(PF_RATES_URL)" \
		PF_PAYROLL_API_KEY="$(PF_PAYROLL_API_KEY)" \
		CORPORATIVE_PIP_INDEX="$(CORPORATIVE_PIP_INDEX)" \
		CORPORATIVE_NPM_REGISTRY="$(CORPORATIVE_NPM_REGISTRY)" \
		CORPORATIVE_PROXY="$(CORPORATIVE_PROXY)" \
		ENV_FILE="$(ENV_FILE)" \
		./scripts/write_env.sh >/dev/null
	@echo "  $(ENV_FILE) written"

.PHONY: local-up
local-up: ## Start full local stack (DB verification, env, deps, API)
	APP_PORT="$(APP_PORT)" \
		VENV="$(VENV)" \
		DB_CONTAINER="$(DB_CONTAINER)" \
		PF_DATABASE_URL="$(PF_DATABASE_URL)" \
		PF_RATES_URL="$(PF_RATES_URL)" \
		PF_PAYROLL_API_KEY="$(PF_PAYROLL_API_KEY)" \
		CORPORATIVE_PIP_INDEX="$(CORPORATIVE_PIP_INDEX)" \
		CORPORATIVE_NPM_REGISTRY="$(CORPORATIVE_NPM_REGISTRY)" \
		CORPORATIVE_PROXY="$(CORPORATIVE_PROXY)" \
		ENV_FILE="$(ENV_FILE)" \
		./scripts/local_stack.sh

.PHONY: import-payroll
import-payroll: ## Import payroll CSV/XLSX file (usage: make import-payroll CSV_FILE=docs/payroll-input.csv)
	@test -n "$(CSV_FILE)" || (echo "CSV_FILE is required. Usage: make import-payroll CSV_FILE=docs/payroll-input.csv" && exit 1)
	PYTHONPATH=src "$(VENV)/bin/python" -m payroll.interfaces.cli.main import-payroll "$(CSV_FILE)"

# Override clean to add service-specific artifacts
.PHONY: clean
clean: ## Remove build artifacts, caches, and service-specific files
	@$(MAKE) -f ../pf-common/make/common.mk clean
	rm -f payroll-dashboard.html
	find . -maxdepth 1 -name "*.pdf" -delete
