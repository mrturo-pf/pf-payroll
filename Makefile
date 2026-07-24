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

DB_CONTAINER ?= pf-db-1
PF_DATABASE_URL ?= postgresql+asyncpg://pf_db:pf_db@localhost:5432/pf_db
PF_RATES_URL ?= http://localhost:8001

# ============================================================================
# Service-specific targets
# ============================================================================

.PHONY: env-write
env-write: ## Write .env file with service-specific defaults
	@printf 'PAYROLL_ENV=development\\n' > $(ENV_FILE)
	@printf 'PF_DATABASE_URL=$(PF_DATABASE_URL)\\n' >> $(ENV_FILE)
	@printf 'PAYROLL_LOG_LEVEL=INFO\\n' >> $(ENV_FILE)
	@printf 'PF_RATES_URL=$(PF_RATES_URL)\\n' >> $(ENV_FILE)
	@printf '# API key that clients must supply as X-API-Key header to access this service.\\n' >> $(ENV_FILE)
	@printf 'PF_PAYROLL_API_KEY=change-me-before-use\\n' >> $(ENV_FILE)
	@printf '\\n# Tooling — corporate pip/npm registries (used by make install/check on VPN)\\n' >> $(ENV_FILE)
	@printf 'CORPORATIVE_PIP_INDEX=https://pypi.ci.artifacts.corporative.com/artifactory/api/pypi/pythonhosted-pypi-release-remote/simple\\n' >> $(ENV_FILE)
	@printf 'CORPORATIVE_NPM_REGISTRY=https://npm.ci.artifacts.corporative.com/artifactory/api/npm/external-npm\\n' >> $(ENV_FILE)
	@printf 'CORPORATIVE_PROXY=http://sysproxy.corpo-rative.com:8080\\n' >> $(ENV_FILE)
	@echo "  $(ENV_FILE) written"

.PHONY: local-up
local-up: ## Start full local stack (DB verification, env, deps, API)
	APP_PORT="$(APP_PORT)" \
		VENV="$(VENV)" \
		DB_CONTAINER="$(DB_CONTAINER)" \
		PF_DATABASE_URL="$(PF_DATABASE_URL)" \
		PF_RATES_URL="$(PF_RATES_URL)" \
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
