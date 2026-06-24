# AGENTS.md — pf-payroll

Chilean payroll simulation and tax calculation suite. Hexagonal (Ports & Adapters) modular monolith with strict financial precision requirements.

## Architecture

Four layers; dependency flows inward only (interfaces → application → domain; infrastructure → application).

```
interfaces/      # FastAPI, Typer CLI, HTML dashboard (adapters in)
application/     # Use cases, ports (Protocols), DTOs, services
domain/          # Entities, value objects, domain services — no I/O
infrastructure/  # SQLAlchemy, importers, WeasyPrint, rate providers (adapters out)
shared/          # Cross-cutting utilities (dates, constants)
```

- `domain/` has zero external dependencies — pure Python dataclasses and domain logic only
- Ports (`application/ports/`) are `typing.Protocol` classes — never import concrete infrastructure types in the application layer
- Use cases are classes with `__init__` accepting port protocols; injected at the interface layer via `interfaces/api/dependencies.py`
- DTOs (`application/dto.py`) are the only data crossing layer boundaries

## Financial precision

- Always use `Decimal`, never `float` for monetary/rate values
- PostgreSQL columns use `NUMERIC`, never `FLOAT`
- CLP quantization helper: `quantize_clp()` in `domain/contribution_calculator.py`
- UTM quantization helper: `quantize_utm()` in `domain/tax_calculator.py`

## Language policy

- All code, identifiers, comments, docstrings, and files: English
- Exception: preserve official Chilean regulatory terms/source literals/seed values in original language only when translation alters meaning

## Code style

- ruff: `extend-select = ["D", "E", "W", "UP"]`, `pep257` convention
- Docstrings required for all modules, classes, and functions only; internal helpers use minimal inline comments
- PEPs: 484 (mypy), 544 (Protocols), 585 (`list[X]`), 604 (`X | None`), 498 (f-strings), 492 (async/await), 654 (ExceptionGroup), 621 (pyproject.toml)
- Domain dataclasses: `@dataclass(slots=True)`; frozen value objects add `frozen=True`
- Async throughout; structlog only (`infrastructure/logging/logger.py`) — never `print` or stdlib `logging`

## Design principles

- Apply DRY, SOLID, Clean Code, DDD — avoid god objects; prefer small, focused classes
- Extract constants/mappings/literals to `shared/`; zero duplication in `src/` or `tests/`
- Thin interface layers (HTTP/CLI/dashboard): orchestration logic belongs in use cases, not routes or commands
- Never `assert` for production validation; raise from `application/errors.py`
- No silent fallbacks

## Development commands

```bash
make local-up              # start DB, write .env, start Adminer, install deps, run API
make db-up                 # start/reuse PostgreSQL (Rancher Desktop)
make env-write             # regenerate .env with default local DB values
make check                 # lint → dead-code → typecheck → dup-check → test → test-cov
# Individual: make lint | dead-code | typecheck | duplicate-code-src | duplicate-code-tests | test | test-cov
```

Run: `source .venv/bin/activate && make check` or `PATH=.venv/bin:$PATH make check`.

## Git hooks

Installed automatically by `make install` via `git config core.hooksPath .githooks`:

| Hook | Runs | Bypass |
|---|---|---|
| `pre-commit` | lint · dead-code · typecheck | `git commit --no-verify` |
| `pre-push` | duplicate-code-src · duplicate-code-tests | `git push --no-verify` |

## Testing conventions

- `tests/unit/` — no DB, no network; `tests/integration/` — live PostgreSQL via testcontainers
- No Mock library. Hand-rolled stub classes per test file. See `tests/unit/application/test_import_payroll.py` for the canonical pattern:

```python
class StubPayrollRepository:
    def __init__(self) -> None:
        self.rows: list[object] = []
    async def import_rows(self, rows):
        self.rows = rows
        return ImportPayrollResultDTO(...)
```

- Shared fixtures go in `tests/helpers/` (e.g., `reference_data.py`, `interface_stubs.py`, `db_fakes.py`) or `tests/conftest.py`
- Verify meaningful outputs (return values, state, errors) — not just that methods were called
- Mark async tests with `@pytest.mark.asyncio`
- 100% coverage required for `src/`

## Adding a new use case

1. Define/extend a port in `application/ports/` using `Protocol`
2. Create use case in `application/use_cases/` — constructor takes port interfaces only
3. Add DTOs to `application/dto.py`
4. Wire dependency in `interfaces/api/dependencies.py` (or CLI equivalent)
5. Add route in `interfaces/api/routes/` or a command in `interfaces/cli/main.py`
6. Add stub-based unit test in `tests/unit/application/`
7. `make check` must pass clean

## CI/CD pipeline

`.github/workflows/deploy.yml` — six jobs:

| Job | Trigger | Action |
|---|---|---|
| `test` | PR + push `main` | lint, vulture, mypy, jscpd, pytest+coverage |
| `build` | PR + push `main` | Docker build, Trivy scan (SARIF + blocking gate on CRITICAL/HIGH) |
| `gate` | push `main` | manual approval via `production` environment |
| `deploy` | push `main` | push image to AR, run Alembic migration job, deploy Cloud Run |
| `notify-failure` | any job failure on `main` | SMTP failure email |
| `notify-success` | successful deploy | SMTP success email |

Pipeline invariants (never violate):

1. Migrations before traffic — `pf-payroll-migrate` Cloud Run Job runs `alembic upgrade head --wait --max-retries=0`
2. DB URL via `--set-secrets` only — never `--set-env-vars`
3. AR scanning stays disabled — pipeline uses Trivy (~$5/month if enabled)
4. `--min-instances=0` — intentional scale-to-zero; do not change without approval
5. Image tagged with both `github.sha` and `latest` — deploy references SHA, not `latest`
6. Non-root container — Dockerfile switches to `appuser` in final stage
7. Multi-stage build — final stage copies only venv + `alembic.ini`; do not add `COPY src ./src`

GitHub Secrets:

| Secret | Purpose |
|---|---|
| `GCP_SA_KEY` | GCP auth (deploy job) |
| `GCP_PROJECT_ID` | image tags, IAM |
| `PAYROLL_DATABASE_URL` | Cloud Run `--set-secrets` |
| `GCP_CLOUD_SQL_INSTANCE` | optional Cloud SQL proxy sidecar |
| `MAIL_SERVER/PORT/USERNAME/PASSWORD/FROM/TO` | SMTP notifications |

DB options: A) External — set `PAYROLL_DATABASE_URL` → Neon/Supabase, leave `GCP_CLOUD_SQL_INSTANCE` empty. B) Cloud SQL — set `GCP_CLOUD_SQL_INSTANCE=PROJECT:us-central1:pf-payroll-db`, pipeline adds proxy sidecar.

Cloud Run: region `us-central1`, min 0/max 2 instances, 512 MiB/1 CPU, port 8000, SA `pf-payroll@<PROJECT>.iam.gserviceaccount.com` needs `roles/secretmanager.secretAccessor`.

## Versioning and operations

- SemVer; Conventional Commits (English)
- Never autonomously commit, push branches, create issues, or open PRs — requires explicit user command

## Database

- Schema: `db/01_schema.sql` (idempotent DDL); base seed: `db/02_seed_base.sql`
- Migrations: Alembic; config: `alembic.ini` → `src/payroll/infrastructure/db/migrations`
- Connection: `PAYROLL_DATABASE_URL` env var (default local: `postgresql+asyncpg://payroll:payroll@localhost:5432/payroll`)
- Sessions: `infrastructure/db/session.py` — always use `async with SessionLocal() as session`
- Repositories implement port `Protocol`s and live in `infrastructure/db/repositories/`
