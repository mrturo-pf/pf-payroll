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

See [`local-development.md`](local-development.md#linting-and-type-checking) for linting, type checking, and coverage commands.

## Where to go next

See the [documentation map](../README.md#documentation-map) for all available guides.
