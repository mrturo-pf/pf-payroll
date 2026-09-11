# Database Guide

Database connection, schema ownership, and local development workflow for pf-payroll.

## Overview

pf-payroll **does not manage its own database**. Schema and migrations are owned by **[pf-db](../../pf-db)** — a separate repository that serves as the single source of truth for all PostgreSQL objects shared across the PF ecosystem.

**Key facts:**
- pf-payroll only holds **SQLAlchemy ORM models** and **repositories**
- Schema changes require a migration in **pf-db** (coordinate with pf-db maintainers)
- Local development uses a shared PostgreSQL instance managed by pf-db
- Production uses the same shared database (Neon, Supabase, or Cloud SQL)

## Connection

### Environment variable

The database connection is configured via the `PF_DATABASE_URL` environment variable:

```bash
# Local (default in .env.example)
PF_DATABASE_URL=postgresql+asyncpg://pf_db:pf_db@localhost:5432/pf_db

# Production (injected via Secret Manager)
PF_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname
```

### Session management

All database access uses **async sessions** via `infrastructure/db/session.py`:

```python
from payroll.infrastructure.db.session import SessionLocal

async with SessionLocal() as session:
    # Use session here
    result = await session.execute(select(PayrollPeriod))
```

**Never create sessions manually** — always use `SessionLocal()` context manager.

## Local database setup

### Step 1: Start pf-db

Navigate to the pf-db repository and start the PostgreSQL container:

```bash
cd ../pf-db
make local-up        # start postgres + apply schema + load base seed
# or
make local-up-test   # same + test fixtures (plans, insurance providers)
```

This starts a PostgreSQL 16 container on `localhost:5432` with:
- All tables created
- Base seed data loaded (RAT_CURRENCY, institutions, caps, brackets, concepts)
- Test fixtures loaded (if using `make local-up-test`)

### Step 2: Start pf-payroll

Navigate back to pf-payroll and start the service:

```bash
cd ../pf-payroll
make local-up        # verifies pf-db is running, writes .env, runs API
```

The `make local-up` target:
1. Checks if pf-db postgres is running (fails if not)
2. Writes `.env` with default local values if it doesn't exist
3. Installs dependencies if `.venv` is missing
4. Starts the FastAPI server

## Table ownership

pf-payroll **owns** the following tables (writes allowed):

### Core payroll tables

| Table | Description |
|---|---|
| `PAY_EMPLOYER` | Employer entities |
| `PAY_PERIOD` | Payroll periods (month/year + payment date) |
| `PAY_PRD_HLTH` | Health plan selections per period |
| `PAY_PRD_COMP` | Complementary insurance per period |
| `PAY_CONCEPT` | Custom payroll concepts (bonuses, deductions) |
| `PAY_ITEM` | Individual payroll line items |

### Reference data tables

| Table | Description |
|---|---|
| `PAY_PENS_INST` | AFP institutions (e.g., Capital, Cuprum, Habitat) |
| `PAY_HLTH_INST` | Health institutions (e.g., Fonasa, Isapres) |
| `PAY_PENS_PLAN` | Pension plan types |
| `PAY_HLTH_PLAN` | Health plan types |
| `PAY_CNTRB_CAP` | Monthly contribution caps (UF-based) |
| `PAY_COMP_PROV` | Insurance providers |
| `PAY_COMP_PLAN` | Insurance plan types |

### Analytics

| View | Type | Description |
|---|---|---|
| `PAY_MV_SUMARY` | Materialized view | Aggregated payroll summaries |

## Tables accessed (read-only)

pf-payroll **reads** these tables owned by [pf-rates](../../pf-rates):

| Table | Owner | Access method |
|---|---|---|
| `RAT_CURRENCY` | pf-rates | **HTTP API only** (never direct SQL) |
| `RAT_EXCH_RATE` | pf-rates | **HTTP API only** |
| `RAT_ECON_INDEX` | pf-rates | **HTTP API only** |
| `RAT_TAX_BRCKT` | pf-rates | **HTTP API only** |

**Important:** pf-payroll accesses financial rates via the **pf-rates HTTP API** — it never queries these tables directly via SQL.

## ORM models

SQLAlchemy models live in `infrastructure/db/models/`:

| File | Tables |
|---|---|
| `payroll.py` | `PAY_EMPLOYER`, `PAY_PERIOD`, `PAY_PRD_HLTH`, `PAY_PRD_COMP`, `PAY_CONCEPT`, `PAY_ITEM`, `PAY_MV_SUMARY` |
| `reference_data.py` | `PAY_PENS_INST`, `PAY_HLTH_INST`, `PAY_PENS_PLAN`, `PAY_HLTH_PLAN`, `PAY_CNTRB_CAP`, `PAY_COMP_PROV`, `PAY_COMP_PLAN` |

### Example: Employer model

```python
from sqlalchemy import String, Integer
from sqlalchemy.orm import Mapped, mapped_column
from payroll.infrastructure.db.models.base import Base

class Employer(Base):
    __tablename__ = "PAY_EMPLOYER"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rut: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
```

## Repositories

Repositories implement **port Protocols** from `application/ports/` and live in `infrastructure/db/repositories/`.

### Example: PayrollRepository

```python
from payroll.application.ports.payroll_repository import PayrollRepository
from payroll.infrastructure.db.session import SessionLocal

class SqlAlchemyPayrollRepository:
    """Implementation of PayrollRepository using SQLAlchemy."""
    
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
    
    async def find_by_id(self, period_id: int) -> PayrollPeriod | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(PayrollPeriodModel).where(PayrollPeriodModel.id == period_id)
            )
            model = result.scalar_one_or_none()
            return self._to_entity(model) if model else None
```

## Schema changes

**Never edit ORM models without a corresponding pf-db migration.**

To add a new column or table:

1. **Coordinate with pf-db maintainers** (or create the migration yourself if you own both repos)
2. **Add migration** in `pf-db/alembic/versions/NNNN_description.py`:
   ```python
   def upgrade() -> None:
       op.execute("""
           ALTER TABLE "PAY_EMPLOYER" 
           ADD COLUMN industry VARCHAR(100);
       """)
   
   def downgrade() -> None:
       op.execute("""
           ALTER TABLE "PAY_EMPLOYER" 
           DROP COLUMN industry;
       """)
   ```
3. **Apply migration** locally:
   ```bash
   cd ../pf-db
   make migrate
   ```
4. **Update ORM model** in pf-payroll:
   ```python
   class Employer(Base):
       # ...
       industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
   ```
5. **Run tests** — ensure integration tests pass
6. **Commit both repos** — pf-db migration first, then pf-payroll model change

## Inspection

### Using psql

Connect to the local database:

```bash
docker exec -it pf-db-postgres psql -U pf_db -d pf_db
```

Common queries:

```sql
-- List all tables
\dt

-- Describe a table
\d "PAY_EMPLOYER"

-- Count payroll periods
SELECT COUNT(*) FROM "PAY_PERIOD";

-- Show recent payroll items
SELECT * FROM "PAY_ITEM" ORDER BY id DESC LIMIT 10;
```

### Using Adminer

Start the Adminer web UI (from pf-db):

```bash
cd ../pf-db
make adminer-up
# Open http://localhost:8081
```

Login:
- System: `PostgreSQL`
- Server: `pf-db-postgres`
- Username: `pf_db`
- Password: `pf_db`
- Database: `pf_db`

## Troubleshooting

### Connection errors: "could not connect to server"

**Cause:** pf-db postgres container is not running.

**Solution:**
```bash
cd ../pf-db
docker ps | grep pf-db-postgres
# If not running:
make local-up
```

### Migration errors: "relation does not exist"

**Cause:** pf-db migrations not applied.

**Solution:**
```bash
cd ../pf-db
make migrate  # applies all pending migrations
```

### Integration tests fail: "testcontainers timeout"

**Cause:** Docker daemon not running or resource constraints.

**Solution:**
1. Ensure Docker Desktop is running
2. Increase Docker memory limit (Preferences → Resources → Memory → 4 GB+)
3. Check Docker logs: `docker ps -a`

### ORM model out of sync with database

**Cause:** Database schema changed without updating ORM model.

**Solution:**
1. Check latest pf-db migrations: `cd ../pf-db && git log --oneline alembic/versions/`
2. Update ORM model to match schema
3. Run `make typecheck` to catch type errors

## Production database

In production, pf-payroll connects to the same shared database instance as pf-rates.

**Database options:**
- **External** (Neon, Supabase): Set `PF_DATABASE_URL` secret in Secret Manager
- **Cloud SQL**: Set `PF_DATABASE_URL` + `GCP_CLOUD_SQL_INSTANCE` secret

**Migrations:** The `pf-db` Cloud Run Job applies `alembic upgrade head` before any service receives traffic. See [Deployment Guide](deployment.md#pipeline-invariants) for details.

## See also

- [pf-db README](../../pf-db/README.md) — Database repository overview
- [pf-db AGENTS.md](../../pf-db/AGENTS.md) — Migration workflow and invariants
- [Development Guide](development.md#database-setup) — Local database setup
- [Deployment Guide](deployment.md#github-secrets) — Production database configuration
