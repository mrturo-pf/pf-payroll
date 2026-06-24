# AGENTS.md — pf-payroll

Chilean payroll simulation and tax calculation suite. Hexagonal (Ports & Adapters) modular monolith with strict financial precision requirements.

---

## Architecture

Four layers; dependency flows inward only (interfaces → application → domain; infrastructure → application).

```
interfaces/      # FastAPI, Typer CLI, HTML dashboard (adapters in)
application/     # Use cases, ports (Protocols), DTOs, services
domain/          # Entities, value objects, domain services — no I/O
infrastructure/  # SQLAlchemy, importers, rate providers, WeasyPrint (adapters out)
shared/          # Cross-cutting utilities (dates, constants)
```

**Key rules:**
- `domain/` has zero external dependencies — pure Python dataclasses and domain logic only
- Ports (`application/ports/`) are `typing.Protocol` classes — never import concrete infra types in the application layer
- Use cases are classes with `__init__` accepting port protocols; injected at the interface layer via `interfaces/api/dependencies.py`
- DTOs (`application/dto.py`) are the only data crossing layer boundaries

## Financial precision

- **Always use `Decimal`, never `float`** for any monetary or rate value
- PostgreSQL columns for money/rates use `NUMERIC`, never `FLOAT`
- CLP quantization helper: `quantize_clp()` in `domain/contribution_calculator.py`
- UTM quantization helper: `quantize_utm()` in `domain/tax_calculator.py`

## Language policy

- All code, identifiers, comments, docstrings, and files must be in **English**
- Exception: preserve official domain/regulatory terms (e.g., Chilean law names), source literals, seed data, and localized UI content in their original language only when translation would alter meaning — surrounding code and docs stay English

## Code style

- PEP 8 + PEP 257 enforced via ruff (`extend-select = ["D", "E", "W", "UP"]`, `convention = "pep257"`)
- Docstrings are required for **public** modules, classes, and functions only; internal helpers use minimal inline comments
- PEPs in force: 484 (types/mypy), 544 (Protocols for ports), 585 (built-in generics `list[X]`), 604 (`X | None` unions), 498 (f-strings), 492 (async/await), 654 (ExceptionGroup), 621 (pyproject.toml)
- Domain dataclasses use `@dataclass(slots=True)`; frozen value objects add `frozen=True`
- Async throughout: all repository and use-case methods are `async def`
- structlog for all logging (`infrastructure/logging/logger.py`) — never use `print` or stdlib `logging` directly

## Design principles

- Apply DRY, SOLID, Clean Code, DDD — avoid god objects; prefer small, focused classes
- Extract repeated constants, mappings, and literals to `shared/` helpers; zero tolerance for duplication in `src/` or `tests/`
- Thin interface layers (HTTP/CLI/dashboard): orchestration logic belongs in use cases, not routes or commands
- Validations must be explicit and placed close to layer boundaries or domain rules
- Never use `assert` for production validation — raise explicit errors from `application/errors.py` for business flow failures; fail loudly, no silent fallbacks

## Development commands

```bash
# Local stack
make local-up              # start DB, write .env, start Adminer, install deps, run API
make db-up                 # start/reuse PostgreSQL (Rancher Desktop)
make env-write             # regenerate .env with default local DB values

# Validation (run before every commit)
make check                 # lint → dead-code → typecheck → dup-check → test → test-cov

# Individual steps
make lint                  # ruff auto-fix + validate
make dead-code             # vulture (unused production code in src/)
make typecheck             # mypy
make duplicate-code-src    # jscpd (0% threshold)
make duplicate-code-tests  # jscpd (0% threshold)
make test                  # pytest
make test-cov              # pytest with 100% coverage enforcement
```

Run with virtualenv active (`source .venv/bin/activate && make check`) or inline (`PATH=.venv/bin:$PATH make check`).

For local validation against real data: `make db-reset-data-real` before running `make check`.

## Git hooks

Installed automatically by `make install` via `git config core.hooksPath .githooks`:

| Hook | Runs | Bypass |
|---|---|---|
| `pre-commit` | lint · dead-code · typecheck | `git commit --no-verify` |
| `pre-push` | duplicate-code-src · duplicate-code-tests | `git push --no-verify` |

## Testing conventions

**Test location:**
- `tests/unit/` — pure unit tests; no database, no network
- `tests/integration/` — live PostgreSQL via testcontainers; uses `TestClient`

**Stub pattern (no Mock library):**
Write hand-rolled stub classes per test file. Do not use `unittest.mock.Mock` or `MagicMock`. See `tests/unit/application/test_import_payroll.py` for the canonical pattern:

```python
class StubPayrollRepository:
    def __init__(self) -> None:
        self.rows: list[object] = []

    async def import_rows(self, rows):
        self.rows = rows
        return ImportPayrollResultDTO(...)
```

**Shared fixtures** go in `tests/helpers/` (e.g., `interface_stubs.py`, `reference_data.py`) or `tests/conftest.py`.

**Assertion quality:** tests must verify meaningful outputs (return values, state, error messages). Avoid assertions that only confirm a method was called.

**Async tests:** mark with `@pytest.mark.asyncio`.

**Coverage:** `src/` requires 100% coverage — every new code path needs a test.

## Adding a new use case

1. Define or extend a port in `application/ports/` using `Protocol`
2. Create the use case class in `application/use_cases/` — constructor takes port interfaces only
3. Add DTOs to `application/dto.py`
4. Wire the dependency in `interfaces/api/dependencies.py` (or CLI equivalent)
5. Add a route in `interfaces/api/routes/` or a command in `interfaces/cli/main.py`
6. Add a stub-based unit test in `tests/unit/application/`
7. Run `make check` — it must pass clean

## Versioning and operations

- **SemVer** for version numbers
- **Conventional Commits** (English) for all git messages
- Follow 12-Factor: configuration via env vars, explicit dependencies, stateless/disposable processes, logs to stdout/stderr
- Never autonomously execute git commits, push branches, create issues, or open PRs — each requires an explicit user command

## Database

- Schema: `db/01_schema.sql` (idempotent DDL)
- Base seed: `db/02_seed_base.sql`
- Migrations via Alembic (`alembic/`); config: `alembic.ini` → `src/payroll/infrastructure/db/migrations`
- Connection: `postgresql+asyncpg://payroll:payroll@localhost:5432/payroll` (default local)
- Sessions: `infrastructure/db/session.py` — always use `async with SessionLocal() as session`
- Repositories implement the port `Protocol`s and live in `infrastructure/db/repositories/`
