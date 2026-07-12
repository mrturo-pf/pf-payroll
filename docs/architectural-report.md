# Reporte Arquitectónico: Administrador de Nómina

**Clasificación del Diseño:** Greenfield, Arquitectura Hexagonal (Modular Monolith), Domain-Driven Design (DDD). Prioridad en precisión financiera estricta, tolerancia a fallos en ingesta manual y portabilidad transparente entre entornos locales (Docker) y Cloud (Managed PostgreSQL).

---

## 1. Definición de Supuestos y Decisiones de Alcance

* **Precisión Financiera:** Cálculo exacto. Uso estricto de `Decimal` en Python y `NUMERIC` en PostgreSQL. Nunca utilizar tipos flotantes.
* **Variables Macroeconómicas:** Monedas (CLP, USD, EUR), Unidades de Reajuste (UF, UTM) y Deflactores (IPC). Soporte nativo para consultas históricas y deflación ($Monto_{Real} = Monto_{Nominal} \times IPC_{Destino} \div IPC_{Origen}$).
* **Módulos de Seguridad Social (Chile):** Modelado histórico e independiente de instituciones (AFP, Isapre, Fonasa), planes contratados y topes imponibles variables. Los cálculos impositivos (Impuesto Único) dependen de la tabla oficial del SII.
* **Inmutabilidad:** Los registros en la tabla de `payroll_periods` actúan como *snapshots*. Si un plan de AFP o Isapre cambia en el futuro, los periodos históricos mantienen los IDs de los planes activos en la fecha de pago original.
* **Persistencia y Portabilidad:** PostgreSQL 16 como motor principal. El esquema es agnóstico del entorno, usando DDL idempotente sin dependencias de extensiones que requieran privilegios de superusuario (para despliegue sin fricción en Neon, Supabase, RDS, etc.).

---

## 2. Diseño de Arquitectura: Monolito Modular

Se utiliza una arquitectura de puertos y adaptadores (Hexagonal) para aislar la lógica de cálculo impositivo y previsional chileno de los mecanismos de entrega (API, CLI, Dashboards) y almacenamiento.

```text
┌────────────────────────────────────────────────────────────────┐
│                   Interfaces (Adapters In)                     │
│  CLI (Typer) │ HTTP API (FastAPI) │ Dashboard (Dash/Streamlit) │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│                     Application Layer                          │
│  Use Cases: ImportPayroll, ComputeContributions, AssignPlans,  │
│             DeflateAmounts, RefreshRates                       │
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
│ PostgreSQL (SQLAlchemy) │ Excel/CSV Importer │ Rate Providers  │
│ Alembic Migrations      │ WeasyPrint Reports │ structlog       │
└────────────────────────────────────────────────────────────────┘
```

---

## 3. Modelo de Datos Completo (PostgreSQL)

Diseño altamente normalizado. Soporta índices económicos, instituciones de salud y pensiones, y consolidación vía vistas materializadas para analítica eficiente.

```sql
-- ============================================================
-- 1. Unidades y Monedas
-- ============================================================
CREATE TABLE IF NOT EXISTS currencies (
    code        CHAR(3) PRIMARY KEY,
    name        VARCHAR(60) NOT NULL,
    is_fiat     BOOLEAN     NOT NULL DEFAULT TRUE,
    unit_kind   VARCHAR(20) NOT NULL DEFAULT 'currency' 
        CHECK (unit_kind IN ('currency', 'index_unit'))
);

CREATE TABLE IF NOT EXISTS exchange_rates (
    id            BIGSERIAL PRIMARY KEY,
    currency_code CHAR(3)         NOT NULL REFERENCES currencies(code),
    rate_date     DATE            NOT NULL,
    value_clp     NUMERIC(18,6)   NOT NULL CHECK (value_clp > 0),
    source        VARCHAR(40)     NOT NULL DEFAULT 'manual',
    created_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (currency_code, rate_date)
);

CREATE TABLE IF NOT EXISTS economic_indices (
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
-- 2. Instituciones y Planes Previsionales/Salud
-- ============================================================
CREATE TABLE IF NOT EXISTS pension_institutions (
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

CREATE TABLE IF NOT EXISTS health_institutions (
    id             BIGSERIAL PRIMARY KEY,
    code           VARCHAR(40)     NOT NULL UNIQUE,
    name           VARCHAR(120)    NOT NULL,
    kind           health_institution_kind NOT NULL,
    mandatory_rate NUMERIC(6,4)    NOT NULL DEFAULT 0.07,
    is_active      BOOLEAN         NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS pension_plans (
    id              BIGSERIAL PRIMARY KEY,
    institution_id  BIGINT          NOT NULL REFERENCES pension_institutions(id),
    valid_from      DATE            NOT NULL,
    valid_to        DATE,
    additional_rate NUMERIC(6,4)    NOT NULL DEFAULT 0 CHECK (additional_rate >= 0),
    CONSTRAINT chk_pension_plan_dates CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS health_plans (
    id              BIGSERIAL PRIMARY KEY,
    institution_id  BIGINT          NOT NULL REFERENCES health_institutions(id),
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

CREATE TABLE IF NOT EXISTS contribution_caps (
    id         BIGSERIAL PRIMARY KEY,
    cap_type   contribution_cap_type NOT NULL,
    valid_from DATE            NOT NULL,
    valid_to   DATE,
    value_uf   NUMERIC(10,4)   NOT NULL CHECK (value_uf > 0),
    UNIQUE (cap_type, valid_from)
);

-- ============================================================
-- 3. Core Nómina
-- ============================================================
CREATE TABLE IF NOT EXISTS employers (
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

CREATE TABLE IF NOT EXISTS payroll_periods (
    id              BIGSERIAL PRIMARY KEY,
    employer_id     BIGINT          NOT NULL REFERENCES employers(id),
    period_year     SMALLINT        NOT NULL,
    period_month    SMALLINT        NOT NULL,
    payment_date    DATE            NOT NULL,
    worked_days     SMALLINT        NOT NULL DEFAULT 30,
    status          payroll_status  NOT NULL DEFAULT 'projected',
    pension_plan_id BIGINT          REFERENCES pension_plans(id),
    health_plan_id  BIGINT          REFERENCES health_plans(id),
    UNIQUE (employer_id, period_year, period_month)
);

-- 4. Analytics Vista Materializada (ver definición completa en db/01_schema.sql)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_payroll_summary AS
SELECT
    p.id AS period_id,
    p.employer_id,
    p.period_year,
    p.period_month,
    p.payment_date,
    -- Ingresos imponibles, brutos, descuentos y neto:
    SUM(CASE WHEN c.kind = 'income' AND c.is_taxable THEN i.amount_clp ELSE 0 END) AS taxable_income_clp,
    SUM(CASE WHEN c.kind = 'income'   THEN i.amount_clp ELSE 0 END) AS gross_income_clp,
    SUM(CASE WHEN c.kind = 'discount' THEN i.amount_clp ELSE 0 END) AS total_discounts_clp,
    -- net_pay = gross - discounts (calculado en la vista)
    SUM(CASE WHEN c.kind = 'income'   THEN i.amount_clp ELSE 0 END)
  - SUM(CASE WHEN c.kind = 'discount' THEN i.amount_clp ELSE 0 END) AS net_pay_clp
FROM payroll_periods p
JOIN payroll_items   i ON i.period_id = p.id
JOIN payroll_concepts c ON c.id = i.concept_id
GROUP BY p.id;
```

---

## 4. Lógica de Dominio: Motor de Contribuciones

El código maneja topes imponibles, límites de UF y el diferencial específico para el recargo de planes Isapre. Python `Decimal` se usa para prevenir imprecisiones por coma flotante.

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
        # Aplica tope imponible (igual que pension)
        cap_clp = _quantize_clp(cap.value_uf * uf_value_clp)
        capped_base = min(taxable_clp, cap_clp)
        base_amount = _quantize_clp(capped_base * plan.institution.mandatory_rate)

        # Isapre: recargo sobre el plan contratado; Fonasa: sin recargo adicional
        if plan.institution.kind is HealthInstitutionKind.ISAPRE and plan.contracted_uf > 0:
            contracted_clp = _quantize_clp(plan.contracted_uf * uf_value_clp)
            additional_amount = max(Decimal("0"), contracted_clp - base_amount)
        else:
            contracted_clp, additional_amount = Decimal("0"), Decimal("0")

        # Ver src/payroll/domain/contribution_calculator.py para la implementación completa
        return HealthContribution(
            institution_code=plan.institution.code,
            institution_kind=plan.institution.kind,
            taxable_clp=taxable_clp, cap_clp=cap_clp, capped_base_clp=capped_base,
            base_amount_clp=base_amount, contracted_uf=plan.contracted_uf,
            contracted_clp=contracted_clp, additional_amount_clp=additional_amount,
        )
```

---

## 5. Adaptadores de Ingesta (Excel Pivot/ETL)

Para tolerar la planilla histórica (formato horizontal) y mapearlo al dominio puro. Se utiliza `pandas` y una correspondencia de columnas a entidades.

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

---

## 6. Resiliencia de Orígenes de Datos (Fallback Chain)

Se utiliza el patrón de diseño "Chain of Responsibility" mediante el `ChainedFxProvider`. Consultará en orden jerárquico (BCCh -> SII -> Mindicador) tolerando degradaciones de red.

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

## 7. Runbook: Portabilidad y Migración a la Nube

La arquitectura permite mantener la Base de Datos libre de vendor lock-in y extensiones exclusivas de la nube.

| Proveedor | Ventajas principales | Adecuación al caso |
| --- | --- | --- |
| **Neon Serverless** | Ramas de bases de datos, Pausas en inactividad, Free Tier sólido. | Ideal. Permite testing destructivo usando rama separada sin afectar prod. |
| **Supabase** | Ecosistema completo, Auth/Storage incluido, PostgreSQL 15+. | Excelente si se requiere interfaz de administración web (Studio). |
| **AWS Aurora v2** | Resiliencia enterprise, Integración profunda AWS. | Excesivo (costo alto ~USD 45/mes mínimo). Evitar para uso personal. |

### Pasos de Migración (Local a Managed PostgreSQL)

```bash
# 1. Volcado seguro de la base de datos local
pg_dump \
    --host=localhost \
    --username=payroll \
    --dbname=payroll \
    --format=custom \
    --no-owner \
    --no-privileges \
    --compress=9 \
    --file=payroll-$(date +%Y%m%d).dump

# 2. Configurar el endpoint de la nube en la sesión del Shell
export TARGET_DSN="postgresql://[USER]:[PASS]@[NEON-HOST]/payroll?sslmode=require"
export PF_DATABASE_URL="postgresql+asyncpg://${TARGET_DSN#postgresql://}"

# 3. Aplicar migraciones (DML + Idempotency)
alembic upgrade head

# 4. Restauración de Datos (Data-only, ignorando secuencias owner)
pg_restore \
    --dbname="$TARGET_DSN" \
    --data-only \
    --disable-triggers \
    --no-owner \
    --no-privileges \
    --jobs=4 \
    payroll-$(date +%Y%m%d).dump

# 5. Validación y recompilación de Analytics
psql "$TARGET_DSN" -c "REINDEX DATABASE payroll; ANALYZE;"
psql "$TARGET_DSN" -c "REFRESH MATERIALIZED VIEW CONCURRENTLY mv_payroll_summary;"
```

---

## 8. Verificación y Calidad de Software

Para CI/CD en GitHub Actions se utiliza `pytest` + `testcontainers` ejecutando tests sobre instancias reales de PostgreSQL efímero, `mypy` para validación estática de tipos, y validación estricta de dominios matemáticos usando errores explícitos en código productivo y aserciones normales en tests.

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