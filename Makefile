PYTHON ?= python3
VENV ?= .venv
NERDCTL ?= nerdctl
DB_CONTAINER ?= pf-payroll-postgres
DB_VOLUME ?= pf-payroll-postgres-data
DB_NAME ?= payroll
DB_USER ?= payroll
DB_PASSWORD ?= payroll
DB_PORT ?= 5432
ADMINER_CONTAINER ?= pf-payroll-adminer
ADMINER_PORT ?= 8080
APP_PORT ?= 8000
ENV_FILE ?= .env

DB_ENV = NERDCTL_BIN="$(NERDCTL)" DB_CONTAINER="$(DB_CONTAINER)" DB_VOLUME="$(DB_VOLUME)" DB_NAME="$(DB_NAME)" DB_USER="$(DB_USER)" DB_PASSWORD="$(DB_PASSWORD)" DB_PORT="$(DB_PORT)"
DB_SEED_FLAG_base =
DB_SEED_FLAG_test = APPLY_TEST_SEED=1
DB_SEED_FLAG_real = APPLY_REAL_SEED=1
UNSET_PROXY_VARS = bash -eu -o pipefail -c 'vars=(http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY); for v in "$${vars[@]}"; do if [[ -n "$${!v-}" ]]; then printf "  ✓ Unsetting %s → %s\n" "$$v" "$${!v}"; unset "$$v"; else printf "  • %s not set\n" "$$v"; fi; done'

# Creates the local virtual environment and installs project dependencies.
install:
	$(PYTHON) -m venv $(VENV)
	bash -eu -o pipefail -c 'vars=(http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY); for v in "$${vars[@]}"; do if [[ -n "$${!v-}" ]]; then printf "  ✓ Unsetting %s → %s\n" "$$v" "$${!v}"; unset "$$v"; else printf "  • %s not set\n" "$$v"; fi; done; . "$(VENV)/bin/activate" && python -m pip install -U pip && python -m pip install -e ".[dev]"'

# Runs the DB script with a selected action and optional seed mode.
_db-flow:
	$(DB_ENV) $(DB_SEED_FLAG_$(SEED_MODE)) ./scripts/rancher_db.sh $(DB_ACTION)

# Starts PostgreSQL with the base schema and seed data.
db-up:
	$(MAKE) --no-print-directory _db-flow DB_ACTION=up SEED_MODE=base

# Starts PostgreSQL and also loads test fixtures.
db-up-test:
	$(MAKE) --no-print-directory _db-flow DB_ACTION=up SEED_MODE=test

# Starts PostgreSQL and also loads real operational seed data.
db-up-real:
	$(MAKE) --no-print-directory _db-flow DB_ACTION=up SEED_MODE=real

# Recreates the schema and reloads only base seed data.
db-reset-data:
	$(MAKE) --no-print-directory _db-flow DB_ACTION=reset-data SEED_MODE=base

# Recreates the schema and reloads base + test seed data.
db-reset-data-test:
	$(MAKE) --no-print-directory _db-flow DB_ACTION=reset-data SEED_MODE=test

# Recreates the schema and reloads base + real operational seed data.
db-reset-data-real:
	$(MAKE) --no-print-directory _db-flow DB_ACTION=reset-data SEED_MODE=real

# Stops and removes the PostgreSQL container.
db-down:
	$(MAKE) --no-print-directory _db-flow DB_ACTION=down SEED_MODE=base

# Opens an interactive psql session inside the PostgreSQL container.
db-psql:
	$(MAKE) --no-print-directory _db-flow DB_ACTION=psql SEED_MODE=base

# Starts Adminer after ensuring PostgreSQL is up.
adminer-up: db-up
	NERDCTL_BIN="$(NERDCTL)" ADMINER_CONTAINER="$(ADMINER_CONTAINER)" ADMINER_PORT="$(ADMINER_PORT)" ./scripts/adminer.sh up

# Stops and removes the Adminer container.
adminer-down:
	NERDCTL_BIN="$(NERDCTL)" ADMINER_CONTAINER="$(ADMINER_CONTAINER)" ./scripts/adminer.sh down

# Writes a local .env file with database connection defaults.
env-write:
	DB_NAME="$(DB_NAME)" DB_USER="$(DB_USER)" DB_PASSWORD="$(DB_PASSWORD)" DB_PORT="$(DB_PORT)" ENV_FILE="$(ENV_FILE)" ./scripts/write_env.sh

# Unsets common proxy variables in the current shell invocation.
unset-proxy-vars:
	@$(UNSET_PROXY_VARS)

# Brings up the full local stack (DB, Adminer, env, deps, and API).
local-up:
	NERDCTL_BIN="$(NERDCTL)" DB_CONTAINER="$(DB_CONTAINER)" DB_VOLUME="$(DB_VOLUME)" DB_NAME="$(DB_NAME)" DB_USER="$(DB_USER)" DB_PASSWORD="$(DB_PASSWORD)" DB_PORT="$(DB_PORT)" ADMINER_CONTAINER="$(ADMINER_CONTAINER)" ADMINER_PORT="$(ADMINER_PORT)" APP_PORT="$(APP_PORT)" VENV="$(VENV)" ENV_FILE="$(ENV_FILE)" ./scripts/local_stack.sh

# Runs the FastAPI server in development mode with auto-reload.
run:
	uvicorn payroll.interfaces.api.main:app --reload

# Runs the complete test suite.
test:
	pytest

# Runs the test suite with coverage and enforces 100% coverage.
test-cov:
	pytest --cov=src/payroll --cov-report=term-missing --cov-fail-under=100

# Executes all repository quality gates in sequence.
check:
	@set -e; \
	for target in lint dead-code typecheck duplicate-code-src duplicate-code-tests test test-cov; do \
		echo "==> make $$target"; \
		if ! $(MAKE) --no-print-directory $$target; then \
			echo "FAILED: $$target"; \
			exit 1; \
		fi; \
	done; \
	echo "All checks passed."

# Detects duplicated code in tests with a 10% threshold.
duplicate-code-tests:
	$(MAKE) --no-print-directory _duplicate-code DUPLICATE_PATH=tests DUPLICATE_THRESHOLD=10

# Detects duplicated code in src with a 1% threshold.
duplicate-code-src:
	$(MAKE) --no-print-directory _duplicate-code DUPLICATE_PATH=src DUPLICATE_THRESHOLD=1

# Runs jscpd with configurable path and threshold.
_duplicate-code:
	npx --yes jscpd --mode strict --min-lines 10 --min-tokens 70 --threshold $(DUPLICATE_THRESHOLD) --reporters console --ignore "**/.venv/**,**/build/**,**/dist/**" $(DUPLICATE_PATH)

# Runs Ruff autofixes/formatting and then validates lint cleanliness.
lint:
	ruff check --fix --exit-zero src tests
	ruff format src tests
	ruff check src tests

# Reports potentially unused code via Vulture.
dead-code:
	vulture --config pyproject.toml

# Runs static type checking with mypy.
typecheck:
	mypy --install-types --non-interactive src

# Removes local caches and build artifacts.
clean:
	rm -rf .coverage htmlcov .pytest_cache .mypy_cache .ruff_cache build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name "*.egg-info" -prune -exec rm -rf {} +
