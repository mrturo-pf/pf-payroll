# Getting started

## Requirements

- Python 3.12+
- `pip`

## Installation

Install the project with development dependencies:

```bash
make install
```

Equivalent manual setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

## First local run

Activate the virtual environment:

```bash
source .venv/bin/activate
```

Start the local database:

```bash
make db-up
```

If you also want local test-only fixtures loaded:

```bash
make db-up-test
```

If you need to wipe local data and rebuild the database contents from scratch:

```bash
make db-reset-data
```

Run the API:

```bash
make run
```

The API is available at:

```text
http://127.0.0.1:8000
```

Open the interactive API docs:

```text
http://127.0.0.1:8000/docs
```

Quick health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## First validation pass

```bash
make lint
make dead-code
make typecheck
make test-cov
```

## Where to go next

- End-to-end payroll flow: [`payroll-workflow.md`](payroll-workflow.md)
- Complete API endpoint inventory, CLI, and dashboard usage: [`interfaces.md`](interfaces.md)
- Local DB and tooling: [`local-development.md`](local-development.md)
