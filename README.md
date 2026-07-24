# pf-payroll

Personal financial suite for Chilean payroll simulation, tax calculation, and historical analytics with focus on architectural integrity and precision.

## Overview

This repository implements a microservice for Chilean payroll operations with:

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
- PostgreSQL persistence (schema managed by pf-db)

## Quick start

See [`docs/getting-started.md`](docs/getting-started.md) to install, set up the database, and run the API.

## Documentation

| Document | Purpose |
| --- | --- |
| [`docs/getting-started.md`](docs/getting-started.md) | Installation, first run, and basic validation |
| [`docs/development.md`](docs/development.md) | Development commands, testing, git hooks, adding features |
| [`docs/deployment.md`](docs/deployment.md) | CI/CD pipeline, Cloud Run deployment, production config |
| [`docs/database.md`](docs/database.md) | Database connection, schema ownership, local setup |
| [`docs/api.md`](docs/api.md) | HTTP API, CLI commands, dashboard |
| [`docs/payroll-workflow.md`](docs/payroll-workflow.md) | End-to-end payroll flow, CSV/XLSX import format |
| [`docs/architectural-report.md`](docs/architectural-report.md) | Architecture report and target design |
| [`AGENTS.md`](AGENTS.md) | AI agent reference: architecture, code style, design principles |