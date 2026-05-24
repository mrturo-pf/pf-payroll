# pf-payroll

Personal financial suite for Chilean payroll simulation, tax calculation, and historical analytics with focus on architectural integrity and precision.

## Overview

This repository implements a modular monolith for Chilean payroll operations with:

- payroll import from CSV/XLSX
- AFP, health, unemployment insurance, and income tax computation
- payroll period review and PDF generation
- FastAPI API, Typer CLI, and an operational HTML dashboard
- PostgreSQL persistence with local Rancher Desktop workflows

## Supported business flow

The core payroll flow currently supported is:

```text
import -> assign plans -> compute contributions -> compute tax -> review -> report PDF
```

## Quick start

1. Install dependencies:

   ```bash
   make install
   ```

2. Start the local PostgreSQL database:

   ```bash
   make db-up
   ```

3. Run the API:

   ```bash
   make run
   ```

4. Open the API docs:

   ```text
   http://127.0.0.1:8000/docs
   ```

## Documentation map

| Document | Purpose |
| --- | --- |
| [`docs/getting-started.md`](docs/getting-started.md) | Installation, first run, and basic validation. |
| [`docs/interfaces.md`](docs/interfaces.md) | Complete API endpoint inventory, CLI commands, and dashboard usage. |
| [`docs/payroll-workflow.md`](docs/payroll-workflow.md) | End-to-end payroll flow, import format, and examples. |
| [`docs/local-development.md`](docs/local-development.md) | Rancher Desktop database workflow, Adminer, testing, linting, and cleanup. |
| [`architectural-report.md`](architectural-report.md) | Architecture report and target design. |

## Interfaces

| Interface | Entry point | Notes |
| --- | --- | --- |
| API | `make run` | FastAPI app with `/docs`, `/redoc`, and JSON endpoints. |
| CLI | `python -m payroll.interfaces.cli.main` | Operational commands for payroll import, calculation, review, and PDF export. |
| Dashboard | `python -m payroll.interfaces.dashboard.app > payroll-dashboard.html` | HTML view showing payroll periods and next business action. |

The **complete API endpoint list** is maintained in [`docs/interfaces.md`](docs/interfaces.md).

## Validation commands

```bash
source .venv/bin/activate
make lint
make dead-code
make typecheck
make test
make test-cov
```

Validation and standards enforcement are split as follows:

- `make lint` uses **Ruff** to auto-fix and validate repository Python style rules, including the adopted **PEP 8** and **PEP 257** conventions.
- `make dead-code` uses **Vulture** to detect potentially unused production code under `src`.
- `make typecheck` uses **mypy** to validate the repository typing baseline based on **PEP 484**, **PEP 544**, **PEP 585**, and **PEP 604**.
- Project metadata is maintained in `pyproject.toml` using **PEP 621** fields.

The project requires **100% coverage** for `src/payroll`.

## Engineering policy

This repository adopts the following engineering standards and conventions:

- **PEP 8** for Python style and formatting.
- **PEP 257** for module, package, class, function, method, and script docstrings.
- **PEP 484** for type hints across public contracts and application flows.
- **PEP 544** for structural contracts via `Protocol` in application ports.
- **PEP 585** for built-in generic types such as `list[str]`.
- **PEP 604** for union syntax such as `X | None`.
- **PEP 498** for preferred string interpolation via f-strings.
- **PEP 492** for explicit asynchronous I/O with `async` / `await`.
- **PEP 654** when concurrent failures need to be aggregated and surfaced together.
- **SemVer** for project versioning.
- **Twelve-Factor** principles for configuration, dependency declaration, disposability, stateless execution, and logging.

## Repository structure

- `src/payroll/domain`: entities, value objects, and calculation rules
- `src/payroll/application`: use cases and ports
- `src/payroll/infrastructure`: database, importers, rate providers, reporting, logging
- `src/payroll/interfaces`: FastAPI, Typer CLI, and dashboard entrypoints
- `tests`: unit and integration coverage
- `db`: SQL schema and seed data

## Next reading

If you want to operate the system end to end, start here:

1. [`docs/getting-started.md`](docs/getting-started.md)
2. [`docs/payroll-workflow.md`](docs/payroll-workflow.md)
3. [`docs/interfaces.md`](docs/interfaces.md)
