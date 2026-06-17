# pf-payroll

Personal financial suite for Chilean payroll simulation, tax calculation, and historical analytics with focus on architectural integrity and precision.

## Overview

This repository implements a modular monolith for Chilean payroll operations with:

- payroll import from CSV/XLSX
- support for taxable imported income items such as legal gratuity, vacation
  incentive, holiday bonus, availability bonus, legal gratuity adjustment, and
  prior salary difference
- support for imported discount items such as health insurance, bonus advances,
  salary advances, and prior-month leave or absence adjustments
- employer-level payment-date configuration for future payroll projections, with
  a default rule of the last Chilean business day of the remuneration month
- AFP, health, unemployment insurance, and income tax computation
- payroll period review and PDF generation
- FastAPI API, Typer CLI, and an operational HTML dashboard
- PostgreSQL persistence with local Rancher Desktop workflows

## Quick start

See [`docs/getting-started.md`](docs/getting-started.md) to install, set up the database, and run the API.

## Documentation map

| Document | Purpose |
| --- | --- |
| [`docs/getting-started.md`](docs/getting-started.md) | Installation, first run, and basic validation. |
| [`docs/interfaces.md`](docs/interfaces.md) | Complete API endpoint inventory, CLI commands, and dashboard usage. |
| [`docs/payroll-workflow.md`](docs/payroll-workflow.md) | End-to-end payroll flow, full CSV/XLSX import format, supported taxable income columns, and examples. |
| [`docs/local-development.md`](docs/local-development.md) | Rancher Desktop database workflow, Adminer, testing, linting, and cleanup. |
| [`docs/architectural-report.md`](docs/architectural-report.md) | Architecture report and target design. |

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
- `db`: SQL schema, default seed data, and test-only seed data

