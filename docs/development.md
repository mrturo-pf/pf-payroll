# Development Guide

Local development workflow, testing conventions, and contribution guidelines for pf-payroll.

## Prerequisites

1. **Python 3.12+** with `uv` or `pip`
2. **pf-db running** — see [Database Setup](#database-setup)
3. **Virtual environment** — created by `make install`

## Development commands

All commands assume you're in an activated virtualenv:

```bash
source .venv/bin/activate
```

Or prefix with the virtualenv path:

```bash
PATH=.venv/bin:$PATH make <target>
```

### Common workflows

| Command | Description |
|---|---|
| `make install` | Create `.venv`, install dependencies, configure git hooks |
| `make local-up` | Check pf-db is running, write `.env`, install deps, start API |
| `make env-write` | Regenerate `.env` with default local DB values |
| `make check` | **Full validation** — lint → dead-code → typecheck → dup-check → test → test-cov |
| `make run` | Start FastAPI with auto-reload (port 8000) |
| `make cli` | Launch Typer CLI interactive shell |

### Quality checks (individual)

| Command | Tool | Purpose |
|---|---|---|
| `make lint` | ruff | Check style + format code |
| `make dead-code` | vulture | Detect unused code |
| `make typecheck` | mypy | Static type checking |
| `make duplicate-code-src` | jscpd | Detect duplication in `src/` (fail > 0.5%) |
| `make duplicate-code-tests` | jscpd | Detect duplication in `tests/` (fail > 2%) |
| `make test` | pytest | Run all tests (unit + integration) |
| `make test-cov` | pytest | Run tests + generate coverage report (fail < 100%) |

### Database setup

pf-payroll **does not manage its own database**. Start the shared PostgreSQL instance from the [pf-db](../pf-db) repository:

```bash
cd ../pf-db
make local-up        # start postgres + apply schema + load base seed
# or
make local-up-test   # same + test fixtures
```

Then start pf-payroll:

```bash
cd ../pf-payroll
make local-up        # verifies pf-db is running, writes .env, runs API
```

See [Database Guide](database.md) for connection details and table ownership.

## Git hooks

Installed automatically by `make install` via `git config core.hooksPath .githooks`:

| Hook | Runs | Bypass |
|---|---|---|
| `pre-commit` | lint · dead-code · typecheck | `git commit --no-verify` |
| `pre-push` | duplicate-code-src · duplicate-code-tests | `git push --no-verify` |

**Never bypass hooks without justification.** They enforce the same checks that run in CI.

## Testing conventions

### Structure

- `tests/unit/` — no DB, no network; fast, isolated
- `tests/integration/` — live PostgreSQL via testcontainers
- `tests/helpers/` — shared fixtures (`reference_data.py`, `interface_stubs.py`, `db_fakes.py`)
- `tests/conftest.py` — pytest fixtures and configuration

### No Mock library

We use **hand-rolled stub classes** per test file. See `tests/unit/application/test_import_payroll.py` for the canonical pattern:

```python
class StubPayrollRepository:
    def __init__(self) -> None:
        self.rows: list[object] = []
    
    async def import_rows(self, rows):
        self.rows = rows
        return ImportPayrollResultDTO(...)
```

**Why stubs over mocks?**
- More explicit: you see exactly what the stub does
- Type-safe: mypy catches stub mismatches
- No magic: no `MagicMock`, `patch`, or `assert_called_with`

### Test requirements

- **Verify meaningful outputs** — return values, state, errors — not just that methods were called
- **Mark async tests** — `@pytest.mark.asyncio` (`asyncio_mode = "strict"` in `pyproject.toml`)
- **100% coverage required** for `src/` — `make test-cov` fails below 100%
- **Shared fixtures** — go in `tests/helpers/` or `tests/conftest.py`, never duplicate

### Running tests

```bash
# All tests (unit + integration)
make test

# With coverage report
make test-cov

# Run specific test file
pytest tests/unit/domain/test_payroll_period.py

# Run specific test
pytest tests/unit/domain/test_payroll_period.py::test_calculate_afp

# Skip slow integration tests
pytest -m "not integration"
```

## Adding a new use case

Follow this sequence to add new functionality:

1. **Define or extend a port** in `application/ports/` using `Protocol`
   ```python
   from typing import Protocol
   
   class PayrollRepository(Protocol):
       async def find_by_id(self, period_id: int) -> PayrollPeriod | None: ...
   ```

2. **Create use case** in `application/use_cases/` — constructor takes port interfaces only
   ```python
   class GetPayrollPeriod:
       def __init__(self, repository: PayrollRepository) -> None:
           self._repository = repository
   ```

3. **Add DTOs** to `application/dto.py`
   ```python
   @dataclass(frozen=True, slots=True)
   class PayrollPeriodDTO:
       id: int
       employer_id: int
       # ...
   ```

4. **Wire dependency** in `interfaces/api/dependencies.py` (or CLI equivalent)
   ```python
   def get_payroll_repository() -> PayrollRepository:
       return SqlAlchemyPayrollRepository(SessionLocal)
   ```

5. **Add route** in `interfaces/api/routes/` or a command in `interfaces/cli/main.py`
   ```python
   @router.get("/payroll-periods/{period_id}")
   async def get_period(
       period_id: int,
       repository: PayrollRepository = Depends(get_payroll_repository)
   ):
       use_case = GetPayrollPeriod(repository)
       return await use_case.execute(period_id)
   ```

6. **Add stub-based unit test** in `tests/unit/application/`
   ```python
   class StubPayrollRepository:
       # ... stub implementation
   
   async def test_get_payroll_period():
       stub_repo = StubPayrollRepository()
       use_case = GetPayrollPeriod(stub_repo)
       result = await use_case.execute(period_id=123)
       assert result.id == 123
   ```

7. **Run validation** — `make check` must pass clean

> **Note:** Schema changes (new tables/columns) are managed exclusively by [pf-db](../pf-db). Coordinate with pf-db maintainers if your use case requires database modifications.

## Code style

See [AGENTS.md](../AGENTS.md) sections:
- **Language policy** — English only (except Chilean regulatory terms)
- **Code style** — ruff configuration, docstrings, PEPs
- **Design principles** — DRY, SOLID, Clean Code, DDD
- **Financial precision** — always `Decimal`, never `float`

## Debugging

### API debugging

Start the API with auto-reload:

```bash
make run
# API available at http://localhost:8000
# Swagger UI at http://localhost:8000/docs
```

Add breakpoints in your IDE or use `breakpoint()` in the code.

### CLI debugging

```bash
make cli
# Interactive Typer shell
```

### Database inspection

See [Database Guide](database.md#inspection) for tools and queries.

## Troubleshooting

### `make check` fails

Run individual checks to isolate the issue:

```bash
make lint           # Style/formatting issues
make dead-code      # Unused code
make typecheck      # Type errors
make duplicate-code-src  # Code duplication in src/
make test           # Test failures
make test-cov       # Coverage below 100%
```

### Database connection errors

Ensure pf-db is running:

```bash
cd ../pf-db
docker ps | grep pf-db-postgres
# If not running:
make local-up
```

### Import errors after adding dependencies

```bash
make reinstall      # Wipe caches and reinstall all dependencies
```

## Continuous Integration

All PRs and pushes to `main` run `make check` in CI. See [Deployment Guide](deployment.md) for the full pipeline.
