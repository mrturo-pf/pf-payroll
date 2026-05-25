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

install:
	$(PYTHON) -m venv $(VENV)
	. $(VENV)/bin/activate && python -m pip install -U pip && python -m pip install -e ".[dev]"

db-up:
	NERDCTL_BIN="$(NERDCTL)" DB_CONTAINER="$(DB_CONTAINER)" DB_VOLUME="$(DB_VOLUME)" DB_NAME="$(DB_NAME)" DB_USER="$(DB_USER)" DB_PASSWORD="$(DB_PASSWORD)" DB_PORT="$(DB_PORT)" ./scripts/rancher_db.sh up

db-up-test:
	NERDCTL_BIN="$(NERDCTL)" DB_CONTAINER="$(DB_CONTAINER)" DB_VOLUME="$(DB_VOLUME)" DB_NAME="$(DB_NAME)" DB_USER="$(DB_USER)" DB_PASSWORD="$(DB_PASSWORD)" DB_PORT="$(DB_PORT)" APPLY_TEST_SEED=1 ./scripts/rancher_db.sh up

db-reset-data:
	NERDCTL_BIN="$(NERDCTL)" DB_CONTAINER="$(DB_CONTAINER)" DB_VOLUME="$(DB_VOLUME)" DB_NAME="$(DB_NAME)" DB_USER="$(DB_USER)" DB_PASSWORD="$(DB_PASSWORD)" DB_PORT="$(DB_PORT)" ./scripts/rancher_db.sh reset-data

db-reset-data-test:
	NERDCTL_BIN="$(NERDCTL)" DB_CONTAINER="$(DB_CONTAINER)" DB_VOLUME="$(DB_VOLUME)" DB_NAME="$(DB_NAME)" DB_USER="$(DB_USER)" DB_PASSWORD="$(DB_PASSWORD)" DB_PORT="$(DB_PORT)" APPLY_TEST_SEED=1 ./scripts/rancher_db.sh reset-data

db-reset-data-real:
	NERDCTL_BIN="$(NERDCTL)" DB_CONTAINER="$(DB_CONTAINER)" DB_VOLUME="$(DB_VOLUME)" DB_NAME="$(DB_NAME)" DB_USER="$(DB_USER)" DB_PASSWORD="$(DB_PASSWORD)" DB_PORT="$(DB_PORT)" APPLY_REAL_SEED=1 ./scripts/rancher_db.sh reset-data

db-down:
	NERDCTL_BIN="$(NERDCTL)" DB_CONTAINER="$(DB_CONTAINER)" ./scripts/rancher_db.sh down

db-psql:
	NERDCTL_BIN="$(NERDCTL)" DB_CONTAINER="$(DB_CONTAINER)" DB_NAME="$(DB_NAME)" DB_USER="$(DB_USER)" ./scripts/rancher_db.sh psql

adminer-up: db-up
	NERDCTL_BIN="$(NERDCTL)" ADMINER_CONTAINER="$(ADMINER_CONTAINER)" ADMINER_PORT="$(ADMINER_PORT)" ./scripts/adminer.sh up

adminer-down:
	NERDCTL_BIN="$(NERDCTL)" ADMINER_CONTAINER="$(ADMINER_CONTAINER)" ./scripts/adminer.sh down

env-write:
	DB_NAME="$(DB_NAME)" DB_USER="$(DB_USER)" DB_PASSWORD="$(DB_PASSWORD)" DB_PORT="$(DB_PORT)" ENV_FILE="$(ENV_FILE)" ./scripts/write_env.sh

local-up:
	NERDCTL_BIN="$(NERDCTL)" DB_CONTAINER="$(DB_CONTAINER)" DB_VOLUME="$(DB_VOLUME)" DB_NAME="$(DB_NAME)" DB_USER="$(DB_USER)" DB_PASSWORD="$(DB_PASSWORD)" DB_PORT="$(DB_PORT)" ADMINER_CONTAINER="$(ADMINER_CONTAINER)" ADMINER_PORT="$(ADMINER_PORT)" APP_PORT="$(APP_PORT)" VENV="$(VENV)" ENV_FILE="$(ENV_FILE)" ./scripts/local_stack.sh

run:
	uvicorn payroll.interfaces.api.main:app --reload

test:
	pytest

test-cov:
	pytest --cov=src/payroll --cov-report=term-missing --cov-fail-under=100

check:
	@set -e; \
	for target in lint dead-code typecheck duplicate-code test test-cov; do \
		echo "==> make $$target"; \
		if ! $(MAKE) --no-print-directory $$target; then \
			echo "FAILED: $$target"; \
			exit 1; \
		fi; \
	done; \
	echo "All checks passed."

duplicate-code:
	npx --yes jscpd --mode strict --min-lines 10 --min-tokens 70 --threshold 1 --reporters console --ignore "**/.venv/**,**/build/**,**/dist/**" src tests

lint:
	ruff check --fix --exit-zero src tests
	ruff format src tests
	ruff check src tests

dead-code:
	vulture --config pyproject.toml

typecheck:
	mypy --install-types --non-interactive src

clean:
	rm -rf .coverage htmlcov .pytest_cache .mypy_cache .ruff_cache build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name "*.egg-info" -prune -exec rm -rf {} +
