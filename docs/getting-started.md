# Getting Started

Quick installation and setup guide for pf-payroll local development.

## Prerequisites

- **Python 3.12+** with `uv` or `pip`
- **Docker Desktop** (for pf-db PostgreSQL)
- **Git** (for cloning the repository)

## Step 1: Clone the repository

```bash
git clone <repository-url> pf-payroll
cd pf-payroll
```

## Step 2: Install dependencies

```bash
make install
```

This will:
- Create a virtual environment in `.venv/`
- Install all dependencies from `pyproject.toml`
- Configure git hooks (pre-commit, pre-push)

## Step 3: Start the database

pf-payroll does not manage its own database. Start the shared PostgreSQL instance from [pf-db](../../pf-db):

```bash
cd ../pf-db
make local-up        # start postgres + apply schema + load base seed
```

See [Database Guide](database.md) for more details.

## Step 4: Configure environment

Generate `.env` with default local values:

```bash
cd ../pf-payroll
make env-write
```

This creates `.env` from `.env.example`. **Important:** Edit `.env` and set a secure API key:

```bash
# Edit .env
PF_PAYROLL_API_KEY=your-secure-api-key-here
```

The API key is required for all authenticated endpoints (all except `/health`).

## Step 5: Run the service

```bash
make run
```

The FastAPI service starts on **port 8000** with auto-reload enabled.

## Step 6: Verify installation

### Option A: Swagger UI (Browser)

Open your browser and navigate to:

```
http://localhost:8000/docs
```

1. Click **Authorize** button (top right)
2. Enter your `PF_PAYROLL_API_KEY` from `.env`
3. Click **Authorize** then **Close**
4. Try the `GET /health` endpoint (no auth required)
5. Try the `POST /payroll/import` endpoint (requires auth)

### Option B: curl (Terminal)

```bash
# Health check (no auth required)
curl http://localhost:8000/health

# List payroll periods (requires API key)
curl -H "X-API-Key: your-api-key-here" http://localhost:8000/payroll/periods

# Get employer info
curl -H "X-API-Key: your-api-key-here" http://localhost:8000/employers/1
```

Expected response for `/health`:

```json
{"status":"ok"}
```

### Option C: CLI (Typer)

pf-payroll also provides a CLI for administrative tasks:

```bash
make cli
# Interactive shell with available commands
```

### Option D: Dashboard (HTML)

The service includes an operational dashboard:

```
http://localhost:8000/dashboard
```

The dashboard provides:
- Payroll period overview
- Recent imports
- System health metrics

## Step 7: Run tests

```bash
make test
```

This runs all unit and integration tests. For coverage:

```bash
make test-cov
```

Expected output: 100% coverage on `src/`.

## Next steps

- [Development Guide](development.md) - Make commands, testing, git hooks
- [Database Guide](database.md) - Schema, tables, local setup
- [Deployment Guide](deployment.md) - CI/CD, Cloud Run deployment
- [API Reference](api.md) - HTTP API, CLI commands, dashboard
- [Payroll Workflow](payroll-workflow.md) - End-to-end payroll flow
- [Architectural Report](architectural-report.md) - Architecture design and target state

## Troubleshooting

### `make install` fails

**Error:** `uv not found` or `pip install fails`

**Solution:**
```bash
# Option 1: Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Option 2: Use pip directly
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Database connection error

**Error:** `could not connect to server`

**Solution:** Ensure pf-db postgres is running:
```bash
cd ../pf-db
docker ps | grep pf-db-postgres
# If not running:
make local-up
```

### Port 8000 already in use

**Error:** `Address already in use`

**Solution:**
```bash
# Find process using port 8000
lsof -i :8000
# Kill it
kill -9 <PID>
# Or use a different port
uvicorn payroll.interfaces.api.main:app --port 8002 --reload
```

### API key not working

**Error:** `403 Forbidden` when calling endpoints

**Solution:**
1. Check `.env` file contains `PF_PAYROLL_API_KEY=...`
2. Restart the service after editing `.env`
3. In Swagger UI, use the **Authorize** button (not query param)
4. In curl, use header: `-H "X-API-Key: your-key"`

### pf-rates connection error

**Error:** `Failed to connect to pf-rates`

**Solution:** Start the pf-rates service (required for tax calculations):
```bash
cd ../pf-rates
make local-up
```

Check `PF_RATES_URL` in `.env` points to the correct URL (default: `http://localhost:8001`).

### Integration tests fail: "testcontainers timeout"

**Cause:** Docker daemon not running or resource constraints.

**Solution:**
1. Ensure Docker Desktop is running
2. Increase Docker memory limit (Preferences → Resources → Memory → 4 GB+)
3. Check Docker logs: `docker ps -a`

## Interactive Documentation

Once running, access:

- **Swagger UI:** `http://localhost:8000/docs` (interactive API explorer)
- **ReDoc:** `http://localhost:8000/redoc` (alternative documentation view)
- **Dashboard:** `http://localhost:8000/dashboard` (operational metrics)

All provide the same API specification but with different UIs.
