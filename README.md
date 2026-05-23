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
make typecheck
make test
make test-cov
```

The project requires **100% coverage** for `src/payroll`.

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
