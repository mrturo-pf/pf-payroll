# Architectural Report: Payroll Manager

**Design classification:** Greenfield, Hexagonal Architecture (Modular Monolith), Domain-Driven Design (DDD). Priority on strict financial precision, fault tolerance on manual ingestion, and transparent portability between local (Docker) and Cloud (Managed PostgreSQL) environments.

---

## 1. Assumption Definitions and Scope Decisions

* **Financial precision:** exact calculation. Strict use of `Decimal` in Python and `NUMERIC` in PostgreSQL. Never use floating-point types.
* **Macroeconomic variables:** currencies (CLP, USD, EUR), inflation-adjustment units (UF, UTM), and deflators (CPI). Native support for historical queries and deflation ($Amount_{Real} = Amount_{Nominal} \times CPI_{Target} \div CPI_{Origin}$).
* **Social security modules (Chile):** historical, institution-independent modeling (AFP, Isapre, Fonasa), contracted plans, and variable taxable-income caps. Tax calculations (single income tax) depend on the SII's official table.
* **Immutability:** records in the `PAY_PERIOD` table act as *snapshots*. If an AFP or Isapre plan changes in the future, historical periods keep the IDs of whichever plans were active on the original payment date.
* **Persistence and portability:** PostgreSQL 16 as the primary engine. The schema is environment-agnostic, using idempotent DDL with no dependency on extensions that require superuser privileges (for frictionless deployment on Neon, Supabase, RDS, etc.).

---

## 2. Architecture Design: Modular Monolith

A ports-and-adapters (hexagonal) architecture is used to isolate Chilean tax and social-security calculation logic from delivery mechanisms (API, CLI) and storage.

```text
┌────────────────────────────────────────────────────────────────┐
│                   Interfaces (Adapters In)                     │
│              CLI (Typer)      │      HTTP API (FastAPI)        │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│                     Application Layer                          │
│  Use Cases: ImportPayroll, PreviewPdfImport,                   │
│  ComputeContributions, AssignPlans, DeflateAmounts,            │
│  RefreshRates                                                  │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│                       Domain Layer                             │
│  Entities: PayrollPeriod, IncomeItem, PensionPlan, HealthPlan  │
│  Value Objects: Money, IndexPoint, UtmQuote, ContributionCap   │
│  Domain Services: ChileanTaxCalculator, ContributionCalculator │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│                Infrastructure (Adapters Out)                   │
│   PostgreSQL (SQLAlchemy) │ Excel/CSV │ PDF │ Rate Providers   │
│    Alembic Migrations      │ WeasyPrint Reports │ structlog    │
└────────────────────────────────────────────────────────────────┘
```

---

## 3. Complete Data Model (PostgreSQL)

Highly normalized design. Supports economic indices, health and pension institutions, and consolidation via materialized views for efficient analytics.

```sql
-- ============================================================
-- 1. Units and Currencies
-- ============================================================
CREATE TABLE IF NOT EXISTS "RAT_CURRENCY" (
    code        CHAR(3) PRIMARY KEY,
    name        VARCHAR(60) NOT NULL,
    is_fiat     BOOLEAN     NOT NULL DEFAULT TRUE,
    unit_kind   VARCHAR(20) NOT NULL DEFAULT 'currency' 
        CHECK (unit_kind IN ('currency', 'index_unit'))
);

CREATE TABLE IF NOT EXISTS "RAT_EXCH_RATE" (
    id            BIGSERIAL PRIMARY KEY,
    currency_code CHAR(3)         NOT NULL REFERENCES "RAT_CURRENCY"(code),
    rate_date     DATE            NOT NULL,
    value_clp     NUMERIC(18,6)   NOT NULL CHECK (value_clp > 0),
    source        VARCHAR(40)     NOT NULL DEFAULT 'manual',
    created_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (currency_code, rate_date)
);

CREATE TABLE IF NOT EXISTS "RAT_ECON_INDEX" (
    id             BIGSERIAL PRIMARY KEY,
    code           VARCHAR(20)     NOT NULL, -- e.g., IPC_CL
    period_year    SMALLINT        NOT NULL CHECK (period_year BETWEEN 1990 AND 2100),
    period_month   SMALLINT        NOT NULL CHECK (period_month BETWEEN 1 AND 12),
    index_value    NUMERIC(12,6)   NOT NULL CHECK (index_value > 0),
    monthly_change NUMERIC(7,4),
    yearly_change  NUMERIC(7,4),
    base_period    VARCHAR(10)     NOT NULL DEFAULT 'DIC-2018',
    source         VARCHAR(40)     NOT NULL DEFAULT 'manual',
    created_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_economic_indices UNIQUE (code, period_year, period_month)
);

-- ============================================================
-- 2. Social Security / Health Institutions and Plans
-- ============================================================
CREATE TABLE IF NOT EXISTS "PAY_PENS_INST" (
    id             BIGSERIAL PRIMARY KEY,
    code           VARCHAR(40)     NOT NULL UNIQUE,
    name           VARCHAR(120)    NOT NULL,
    mandatory_rate NUMERIC(6,4)    NOT NULL DEFAULT 0.10,
    is_active      BOOLEAN         NOT NULL DEFAULT TRUE
);

DO $$ BEGIN
    CREATE TYPE health_institution_kind AS ENUM ('fonasa', 'isapre');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS "PAY_HLTH_INST" (
    id             BIGSERIAL PRIMARY KEY,
    code           VARCHAR(40)     NOT NULL UNIQUE,
    name           VARCHAR(120)    NOT NULL,
    kind           health_institution_kind NOT NULL,
    mandatory_rate NUMERIC(6,4)    NOT NULL DEFAULT 0.07,
    is_active      BOOLEAN         NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS "PAY_PENS_PLAN" (
    id              BIGSERIAL PRIMARY KEY,
    institution_id  BIGINT          NOT NULL REFERENCES "PAY_PENS_INST"(id),
    valid_from      DATE            NOT NULL,
    valid_to        DATE,
    additional_rate NUMERIC(6,4)    NOT NULL DEFAULT 0 CHECK (additional_rate >= 0),
    CONSTRAINT chk_pension_plan_dates CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS "PAY_HLTH_PLAN" (
    id              BIGSERIAL PRIMARY KEY,
    institution_id  BIGINT          NOT NULL REFERENCES "PAY_HLTH_INST"(id),
    valid_from      DATE            NOT NULL,
    valid_to        DATE,
    plan_name       VARCHAR(120),
    contracted_uf   NUMERIC(10,4)   NOT NULL DEFAULT 0 CHECK (contracted_uf >= 0),
    CONSTRAINT chk_health_plan_dates CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

DO $$ BEGIN
    CREATE TYPE contribution_cap_type AS ENUM ('pension_health', 'unemployment');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS "PAY_CNTRB_CAP" (
    id         BIGSERIAL PRIMARY KEY,
    cap_type   contribution_cap_type NOT NULL,
    valid_from DATE            NOT NULL,
    valid_to   DATE,
    value_uf   NUMERIC(10,4)   NOT NULL CHECK (value_uf > 0),
    UNIQUE (cap_type, valid_from)
);

-- ============================================================
-- 3. Payroll Core
-- ============================================================
CREATE TABLE IF NOT EXISTS "PAY_EMPLOYER" (
    id           BIGSERIAL PRIMARY KEY,
    name         VARCHAR(120)  NOT NULL UNIQUE,
    tax_id       VARCHAR(32),
    country_code CHAR(2)       NOT NULL DEFAULT 'CL',
    started_at   DATE          NOT NULL
);

DO $$ BEGIN
    CREATE TYPE payroll_status AS ENUM ('projected', 'actual', 'reviewed');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS "PAY_PERIOD" (
    id              BIGSERIAL PRIMARY KEY,
    employer_id     BIGINT          NOT NULL REFERENCES "PAY_EMPLOYER"(id),
    period_year     SMALLINT        NOT NULL,
    period_month    SMALLINT        NOT NULL,
    payment_date    DATE            NOT NULL,
    worked_days     SMALLINT        NOT NULL DEFAULT 30,
    status          payroll_status  NOT NULL DEFAULT 'projected',
    pension_plan_id BIGINT          REFERENCES "PAY_PENS_PLAN"(id),
    health_plan_id  BIGINT          REFERENCES "PAY_HLTH_PLAN"(id),
    UNIQUE (employer_id, period_year, period_month)
);

-- 4. Analytics materialized view (see full definition in db/01_schema.sql)
CREATE MATERIALIZED VIEW IF NOT EXISTS "PAY_MV_SUMARY" AS
SELECT
    p.id AS period_id,
    p.employer_id,
    p.period_year,
    p.period_month,
    p.payment_date,
    -- Taxable income, gross income, discounts, and net pay:
    SUM(CASE WHEN c.kind = 'income' AND c.is_taxable THEN i.amount_clp ELSE 0 END) AS taxable_income_clp,
    SUM(CASE WHEN c.kind = 'income'   THEN i.amount_clp ELSE 0 END) AS gross_income_clp,
    SUM(CASE WHEN c.kind = 'discount' THEN i.amount_clp ELSE 0 END) AS total_discounts_clp,
    -- net_pay = gross - discounts (computed in the view)
    SUM(CASE WHEN c.kind = 'income'   THEN i.amount_clp ELSE 0 END)
  - SUM(CASE WHEN c.kind = 'discount' THEN i.amount_clp ELSE 0 END) AS net_pay_clp
FROM "PAY_PERIOD" p
JOIN "PAY_ITEM"   i ON i.period_id = p.id
JOIN "PAY_CONCEPT" c ON c.id = i.concept_id
GROUP BY p.id;
```

---

## 4. Domain Logic: Contribution Engine

The code handles taxable-income caps, UF limits, and the specific differential for the Isapre plan top-up. Python `Decimal` is used to prevent floating-point imprecision.

```python
# src/payroll/domain/contribution_calculator.py
from dataclasses import dataclass
from decimal import Decimal

from .contributions import (
    ContributionCap,
    HealthContribution,
    HealthInstitutionKind,
    HealthPlan,
    PensionContribution,
    PensionPlan,
)

_CLP_QUANT = Decimal("1")


def _quantize_clp(value: Decimal) -> Decimal:
    """Rounds to the nearest integer CLP value."""
    return value.quantize(_CLP_QUANT)


@dataclass(frozen=True, slots=True)
class ContributionCalculator:
    """Computes pension/health deductions honoring strict Chilean caps and mandatory minimums."""

    def pension(
        self, 
        taxable_clp: Decimal, 
        plan: PensionPlan, 
        cap: ContributionCap, 
        uf_value_clp: Decimal
    ) -> PensionContribution:
        cap_clp = _quantize_clp(cap.value_uf * uf_value_clp)
        capped_base = min(taxable_clp, cap_clp)

        base_amount = _quantize_clp(capped_base * plan.institution.mandatory_rate)
        additional_amount = _quantize_clp(capped_base * plan.additional_rate)

        return PensionContribution(
            institution_code=plan.institution.code,
            taxable_clp=taxable_clp,
            cap_clp=cap_clp,
            capped_base_clp=capped_base,
            base_amount_clp=base_amount,
            additional_amount_clp=additional_amount,
        )

    def health(
        self, 
        taxable_clp: Decimal, 
        plan: HealthPlan, 
        cap: ContributionCap, 
        uf_value_clp: Decimal
    ) -> HealthContribution:
        # Applies the taxable-income cap (same as pension)
        cap_clp = _quantize_clp(cap.value_uf * uf_value_clp)
        capped_base = min(taxable_clp, cap_clp)
        base_amount = _quantize_clp(capped_base * plan.institution.mandatory_rate)

        # Isapre: top-up over the contracted plan; Fonasa: no additional top-up
        if plan.institution.kind is HealthInstitutionKind.ISAPRE and plan.contracted_uf > 0:
            contracted_clp = _quantize_clp(plan.contracted_uf * uf_value_clp)
            additional_amount = max(Decimal("0"), contracted_clp - base_amount)
        else:
            contracted_clp, additional_amount = Decimal("0"), Decimal("0")

        # See src/payroll/domain/contribution_calculator.py for the full implementation
        return HealthContribution(
            institution_code=plan.institution.code,
            institution_kind=plan.institution.kind,
            taxable_clp=taxable_clp, cap_clp=cap_clp, capped_base_clp=capped_base,
            base_amount_clp=base_amount, contracted_uf=plan.contracted_uf,
            contracted_clp=contracted_clp, additional_amount_clp=additional_amount,
        )
```

---

## 5. Ingestion Adapters (Excel Pivot/ETL)

To tolerate the historical spreadsheet (wide format) and map it to the pure domain. `pandas` is used along with a column-to-entity mapping.

```python
# src/payroll/infrastructure/importers/xlsx_importer.py
from decimal import Decimal
import pandas as pd

CONCEPT_MAP = {
    "salary_base":            ("SALARY_BASE", "income", True),
    "monthly_legal_gratuity": ("LEGAL_GRATUITY", "income", True),
    "teleworking_refund":     ("TELEWORK_REFUND", "income", False),
    "pension_base":           ("PENSION_BASE", "discount", False),
    "pension_additional":     ("PENSION_ADDITIONAL", "discount", False),
    "health_base":            ("HEALTH_BASE", "discount", False),
    "health_additional_uf":   ("HEALTH_ADDITIONAL_UF", "discount", False),
}


def to_long_format(wide_df: pd.DataFrame) -> pd.DataFrame:
    """Pivots multi-column flat export into normalized application DTO formats."""
    long_rows = []
    
    for _, row in wide_df.iterrows():
        period_str = str(row.get("period", "")).strip()
        if "/" not in period_str: 
            continue
            
        m_str, y_str = period_str.split("/")
        payment_dt = pd.to_datetime(row.get("payment_date"), errors="coerce", dayfirst=True)
        
        base_meta = {
            "employer": row.get("employer"),
            "year": int(y_str),
            "month": pd.to_datetime(m_str, format="%b").month,
            "payment_date": payment_dt.date() if pd.notna(payment_dt) else None,
            "status": "actual" if pd.notna(row.get("net_pay")) else "projected"
        }

        for col, (code, kind, is_tax) in CONCEPT_MAP.items():
            val = row.get(col)
            if pd.notna(val):
                long_rows.append({
                    **base_meta, 
                    "concept_code": code, 
                    "kind": kind,
                    "is_taxable": is_tax, 
                    "amount_clp": Decimal(str(val))
                })
                
    return pd.DataFrame(long_rows)
```

### 5.1 Alternative ingestion: payslip PDF

An alternative to Excel/CSV for a single payslip: extraction based on versioned JSON
templates (`infrastructure/pdf_import/templates/`), never OCR/LLM. Two new HTTP routes
(`POST /payroll/import/pdf-preview`, `POST /payroll/import/rows`) plus the
`PreviewPdfImport` use case, which deliberately receives no repository -- that's the
architectural guarantee that the preview can never write to the database.
Full design and implementation detail in
[`docs/proposals/pdf-import-action-plan.md`](proposals/pdf-import-action-plan.md).

```python
# src/payroll/infrastructure/pdf_import/extractor.py
class TemplatePdfPayrollExtractor(PdfPayrollExtractor):
    """Extracts a payroll PDF preview using versioned JSON templates.

    Never raises: any failure (unparsable PDF, no template match, no detail
    rows found) degrades to an emptier PdfImportPreviewDTO rather than an
    exception.
    """

    def extract_preview(self, filename: str, content: bytes) -> PdfImportPreviewDTO:
        raw_text = extract_raw_text(content)
        detail_lines = find_detail_lines(raw_text)
        template = select_template(self._templates, raw_text, labels)
        # Each detected row is resolved against the template (concept_code +
        # confidence) or is left as concept_code=None, confidence=0.0 --
        # never raises, the preview always responds 200.
        ...
```

---

## 6. Data Source Resilience (Fallback Chain)

The "Chain of Responsibility" design pattern is used via `ChainedFxProvider`. It queries providers in hierarchical order (BCCh -> SII -> Mindicador), tolerating network degradation.

```python
# src/payroll/infrastructure/rate_providers/chained_provider.py
from datetime import date
from decimal import Decimal

import structlog

from payroll.application.ports.rate_provider import FxRateProvider

log = structlog.get_logger(__name__)


class ChainedFxProvider(FxRateProvider):
    name = "chained"

    def __init__(self, providers: list[FxRateProvider]) -> None:
        self._providers = providers

    async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
        for provider in self._providers:
            try:
                value = await provider.fetch_rate(currency_code, on)
                if value is not None:
                    log.info("rate_found", provider=provider.name, currency=currency_code, on=on)
                    return value
            except Exception as exc:
                log.warning("provider_failed", provider=provider.name, error=str(exc))
                continue
        return None
```

---

## 7. Runbook: Portability and Cloud Migration

The architecture keeps the database free of vendor lock-in and cloud-exclusive extensions.

| Provider | Main advantages | Fit for this case |
| --- | --- | --- |
| **Neon Serverless** | Database branching, pause on idle, solid free tier. | Ideal. Allows destructive testing on a separate branch without affecting prod. |
| **Supabase** | Full ecosystem, Auth/Storage included, PostgreSQL 15+. | Excellent if a web admin interface (Studio) is needed. |
| **AWS Aurora v2** | Enterprise resilience, deep AWS integration. | Overkill (high cost, ~USD 45/month minimum). Avoid for personal use. |

### Migration Steps (Local to Managed PostgreSQL)

```bash
# 1. Safe dump of the local database
pg_dump \
    --host=localhost \
    --username=payroll \
    --dbname=payroll \
    --format=custom \
    --no-owner \
    --no-privileges \
    --compress=9 \
    --file=payroll-$(date +%Y%m%d).dump

# 2. Configure the cloud endpoint in the shell session
export TARGET_DSN="postgresql://[USER]:[PASS]@[NEON-HOST]/payroll?sslmode=require"
export PF_DATABASE_URL="postgresql+asyncpg://${TARGET_DSN#postgresql://}"

# 3. Apply migrations (DML + idempotency)
alembic upgrade head

# 4. Data restore (data-only, ignoring owner sequences)
pg_restore \
    --dbname="$TARGET_DSN" \
    --data-only \
    --disable-triggers \
    --no-owner \
    --no-privileges \
    --jobs=4 \
    payroll-$(date +%Y%m%d).dump

# 5. Validation and analytics rebuild
psql "$TARGET_DSN" -c "REINDEX DATABASE payroll; ANALYZE;"
psql "$TARGET_DSN" -c "REFRESH MATERIALIZED VIEW CONCURRENTLY "PAY_MV_SUMARY";"
```

---

## 8. Verification and Software Quality

CI/CD on GitHub Actions uses `pytest` + `testcontainers` running tests against real, ephemeral PostgreSQL instances, `mypy` for static type checking, and strict validation of mathematical domains using explicit errors in production code and plain assertions in tests.

```python
# tests/unit/test_health_additional.py
from datetime import date
from decimal import Decimal

from payroll.domain.contribution_calculator import ContributionCalculator
from payroll.domain.contributions import (
    ContributionCap,
    HealthInstitution,
    HealthInstitutionKind,
    HealthPlan,
)


def test_health_isapre_additional_when_plan_exceeds_seven_percent() -> None:
    calc = ContributionCalculator()
    uf_value = Decimal("40106.89")
    taxable = Decimal("3729340")
    
    cap = ContributionCap("pension_health", date(2026, 1, 1), None, Decimal("90.0600"))
    
    plan = HealthPlan(
        id=1, 
        institution=HealthInstitution(
            code="ISAPRE_A", 
            name="Isapre A", 
            kind=HealthInstitutionKind.ISAPRE, 
            mandatory_rate=Decimal("0.07")
        ), 
        valid_from=date(2024, 1, 1), 
        valid_to=None, 
        plan_name="Premium", 
        contracted_uf=Decimal("7.1224")
    )
    
    result = calc.health(taxable, plan, cap, uf_value)
    
    assert result.base_amount_clp > Decimal("0")
    
    contracted_clp_expected = (Decimal("7.1224") * uf_value).quantize(Decimal("1"))
    assert result.contracted_clp == contracted_clp_expected
        
    expected_additional = max(Decimal("0"), contracted_clp_expected - result.base_amount_clp)
    assert result.additional_amount_clp == expected_additional
```
