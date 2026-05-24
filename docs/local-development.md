# Local development

## Rancher Desktop PostgreSQL

The repository includes a Rancher Desktop flow that:

- creates a local PostgreSQL container if it does not exist
- starts the existing container if it is stopped
- reuses the existing data volume if it already exists
- applies the schema idempotently
- seeds default catalog data idempotently
- loads test-only seed data only when explicitly requested

Start or reuse the local database:

```bash
make db-up
```

Start or reuse the local database with test-only fixtures:

```bash
make db-up-test
```

Reset the local database data, reapply the schema, and reload the base seed data:

```bash
make db-reset-data
```

Reset the local database data and reload both base and test-only seed data:

```bash
make db-reset-data-test
```

Reset the local database data and reload both base and real operational seed data:

```bash
make db-reset-data-real
```

Open a `psql` session inside the running container:

```bash
make db-psql
```

Stop the container without deleting its data volume:

```bash
make db-down
```

Default local connection string:

```text
postgresql+asyncpg://payroll:payroll@localhost:5432/payroll
```

Override defaults when needed:

```bash
make db-up DB_CONTAINER=my-payroll-db DB_PORT=5433 DB_PASSWORD=secret
```

The schema lives in `db/schema.sql`, the default catalog data in `db/seed.sql`, the real operational bootstrap data in `db/seed_real.sql`, and test-only fixtures in `db/seed_test.sql`.

## Adminer

Start Adminer:

```bash
make adminer-up
```

Stop Adminer:

```bash
make adminer-down
```

If the preferred port is busy, the script chooses a free one automatically and prints the exact URL.

## Full local stack

Bring the whole stack up in one command:

```bash
make local-up
```

This will:

- start or reuse PostgreSQL
- write `.env`
- start Adminer
- install dependencies
- run the API

## Testing

Run the full test suite:

```bash
source .venv/bin/activate
make test
```

Run tests with coverage enforcement:

```bash
source .venv/bin/activate
make test-cov
```

The repository requires **100% coverage** for `src/payroll`.

## Linting and type checking

```bash
source .venv/bin/activate
make lint
make dead-code
make typecheck
```

## Cleaning generated files

```bash
make clean
```

This removes:

- `__pycache__`
- `.pytest_cache`
- `.mypy_cache`
- `.ruff_cache`
- `.coverage`
- `htmlcov`
- `build`
- `dist`
- `*.egg-info`
