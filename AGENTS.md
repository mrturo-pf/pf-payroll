# AGENTS.md — pf-payroll

Chilean payroll simulation and tax calculation suite. Microservice with Hexagonal (Ports & Adapters) architecture and strict financial precision requirements.

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
- Quantization helpers: `quantize_clp()` and `quantize_utm()` in `domain/quantizers.py`

## Language policy

- All code, identifiers, comments, docstrings, and files: English
- Exception: preserve official Chilean regulatory terms/source literals/seed values in original language only when translation alters meaning

## Code style

- ruff: `extend-select = ["D", "E", "W", "UP"]`, `pep257` convention
- Docstrings required for all modules, classes, and functions only; internal helpers use minimal inline comments
- PEPs: 484 (mypy), 544 (Protocols), 585 (`list[X]`), 604 (`X | None`), 498 (f-strings), 492 (async/await), 621 (pyproject.toml)
- Domain dataclasses: `@dataclass(slots=True)`; frozen value objects add `frozen=True`
- Async throughout; structlog only (`infrastructure/logging/logger.py`) — never `print` or stdlib `logging`

## Design principles

- Apply DRY, SOLID, Clean Code, DDD — avoid god objects; prefer small, focused classes
- Extract constants/mappings/literals to `shared/`; zero duplication in `src/` or `tests/`
- Thin interface layers (HTTP/CLI/dashboard): orchestration logic belongs in use cases, not routes or commands
- Never `assert` for production validation; raise from `application/errors.py`
- No silent fallbacks

## Development commands

See [`docs/development.md`](docs/development.md) for the complete development workflow:
- `make` commands (install, local-up, check, test, lint, etc.)
- Git hooks (pre-commit, pre-push)
- Testing conventions (stubs, coverage, async)
- Adding a new use case (step-by-step guide)

Quick reference:

```bash
make install               # create .venv, install deps, configure git hooks
make local-up              # start API (requires pf-db running)
make check                 # lint → dead-code → typecheck → dup-check → test → test-cov
```

## CI/CD pipeline

See [`docs/deployment.md`](docs/deployment.md) for the complete deployment guide:
- Pipeline jobs (test, build, gate, deploy, notify)
- Pipeline invariants (migrations before traffic, secrets, scaling, etc.)
- GitHub Secrets configuration
- Cloud Run configuration
- Manual deployment and rollback procedures

Quick reference:

- **Trigger:** Push to `main` (after manual approval via `production` environment)
- **Security:** Trivy scan blocks on CRITICAL/HIGH vulnerabilities
- **Database:** Shared instance managed by [pf-db](../pf-db); migrations applied before deployment
- **Scaling:** min 0 / max 2 instances (scale-to-zero enabled)

## Versioning and operations

- SemVer; Conventional Commits (English)
- Never autonomously commit, push branches, create issues, or open PRs — requires explicit user command

## Database

See [`docs/database.md`](docs/database.md) for the complete database guide:
- Connection configuration (`PF_DATABASE_URL`)
- Local database setup (pf-db workflow)
- Table ownership (which tables pf-payroll writes to)
- ORM models and repositories
- Schema changes (coordination with pf-db)
- Database inspection tools (psql, Adminer)

Quick reference:

- **Schema owner:** [pf-db](../pf-db) (separate repository)
- **Connection:** `postgresql+asyncpg://pf_db:pf_db@localhost:5432/pf_db` (local)
- **Session management:** Always use `async with SessionLocal() as session`
- **Schema changes:** Coordinate with pf-db maintainers; never edit ORM models without a corresponding migration