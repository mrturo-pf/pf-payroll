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
    currency_code CHAR(3)       NOT NULL REFERENCES currencies(code),
    rate_date     DATE          NOT NULL,
    value_clp     NUMERIC(18,6) NOT NULL CHECK (value_clp > 0),
    source        VARCHAR(40)   NOT NULL DEFAULT 'manual',
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (currency_code, rate_date)
);

CREATE TABLE IF NOT EXISTS economic_indices (
    id             BIGSERIAL PRIMARY KEY,
    code           VARCHAR(20)   NOT NULL,
    period_year    SMALLINT      NOT NULL CHECK (period_year BETWEEN 1990 AND 2100),
    period_month   SMALLINT      NOT NULL CHECK (period_month BETWEEN 1 AND 12),
    index_value    NUMERIC(12,6) NOT NULL CHECK (index_value > 0),
    monthly_change NUMERIC(7,4),
    yearly_change  NUMERIC(7,4),
    base_period    VARCHAR(10)   NOT NULL DEFAULT 'DIC-2018',
    source         VARCHAR(40)   NOT NULL DEFAULT 'manual',
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_economic_indices UNIQUE (code, period_year, period_month)
);

CREATE TABLE IF NOT EXISTS income_tax_brackets (
    id                BIGSERIAL PRIMARY KEY,
    valid_from        DATE          NOT NULL,
    valid_to          DATE,
    lower_bound_utm   NUMERIC(10,4) NOT NULL CHECK (lower_bound_utm >= 0),
    upper_bound_utm   NUMERIC(10,4),
    marginal_rate     NUMERIC(8,6)  NOT NULL CHECK (marginal_rate >= 0 AND marginal_rate <= 1),
    rebate_utm        NUMERIC(10,4) NOT NULL DEFAULT 0 CHECK (rebate_utm >= 0),
    CONSTRAINT chk_income_tax_bracket_bounds CHECK (upper_bound_utm IS NULL OR upper_bound_utm > lower_bound_utm),
    UNIQUE (valid_from, lower_bound_utm)
);

-- ============================================================
-- 2. Instituciones y Planes Previsionales/Salud
-- ============================================================
DO $$ BEGIN
    CREATE TYPE health_institution_kind AS ENUM ('fonasa', 'isapre');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE contribution_cap_type AS ENUM ('pension_health', 'unemployment');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS pension_institutions (
    id             BIGSERIAL PRIMARY KEY,
    code           VARCHAR(40)   NOT NULL UNIQUE,
    name           VARCHAR(120)  NOT NULL,
    mandatory_rate NUMERIC(6,4)  NOT NULL DEFAULT 0.10,
    is_active      BOOLEAN       NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS health_institutions (
    id             BIGSERIAL PRIMARY KEY,
    code           VARCHAR(40)               NOT NULL UNIQUE,
    name           VARCHAR(120)              NOT NULL,
    kind           health_institution_kind   NOT NULL,
    mandatory_rate NUMERIC(6,4)              NOT NULL DEFAULT 0.07,
    is_active      BOOLEAN                   NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS pension_plans (
    id              BIGSERIAL PRIMARY KEY,
    institution_id  BIGINT        NOT NULL REFERENCES pension_institutions(id),
    valid_from      DATE          NOT NULL,
    valid_to        DATE,
    additional_rate NUMERIC(6,4)  NOT NULL DEFAULT 0 CHECK (additional_rate >= 0),
    CONSTRAINT chk_pension_plan_dates CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS health_plans (
    id              BIGSERIAL PRIMARY KEY,
    institution_id  BIGINT         NOT NULL REFERENCES health_institutions(id),
    valid_from      DATE           NOT NULL,
    valid_to        DATE,
    plan_name       VARCHAR(120),
    contracted_uf   NUMERIC(10,4)  NOT NULL DEFAULT 0 CHECK (contracted_uf >= 0),
    CONSTRAINT chk_health_plan_dates CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS contribution_caps (
    id         BIGSERIAL PRIMARY KEY,
    cap_type   contribution_cap_type NOT NULL,
    valid_from DATE          NOT NULL,
    valid_to   DATE,
    value_uf   NUMERIC(10,4) NOT NULL CHECK (value_uf > 0),
    UNIQUE (cap_type, valid_from)
);

-- ============================================================
-- 3. Core Nómina
-- ============================================================
DO $$ BEGIN
    CREATE TYPE payroll_status AS ENUM ('projected', 'actual', 'reviewed');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE employment_contract_kind AS ENUM ('indefinite', 'fixed_term');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS employers (
    id           BIGSERIAL PRIMARY KEY,
    name         VARCHAR(120) NOT NULL UNIQUE,
    tax_id       VARCHAR(32),
    country_code CHAR(2)      NOT NULL DEFAULT 'CL',
    started_at   DATE         NOT NULL
);

CREATE TABLE IF NOT EXISTS payroll_periods (
    id              BIGSERIAL PRIMARY KEY,
    employer_id     BIGINT         NOT NULL REFERENCES employers(id),
    period_year     SMALLINT       NOT NULL,
    period_month    SMALLINT       NOT NULL,
    payment_date    DATE           NOT NULL,
    worked_days     SMALLINT       NOT NULL DEFAULT 30,
    status          payroll_status NOT NULL DEFAULT 'projected',
    employment_contract_kind employment_contract_kind NOT NULL DEFAULT 'indefinite',
    pension_plan_id BIGINT         REFERENCES pension_plans(id),
    health_plan_id  BIGINT         REFERENCES health_plans(id),
    UNIQUE (employer_id, period_year, period_month)
);

ALTER TABLE payroll_periods
    ADD COLUMN IF NOT EXISTS employment_contract_kind employment_contract_kind NOT NULL DEFAULT 'indefinite';

CREATE TABLE IF NOT EXISTS payroll_concepts (
    id          BIGSERIAL PRIMARY KEY,
    code        VARCHAR(40)  NOT NULL UNIQUE,
    name        VARCHAR(120) NOT NULL,
    kind        VARCHAR(20)  NOT NULL CHECK (kind IN ('income', 'discount')),
    is_taxable  BOOLEAN      NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS payroll_items (
    id          BIGSERIAL PRIMARY KEY,
    period_id   BIGINT          NOT NULL REFERENCES payroll_periods(id) ON DELETE CASCADE,
    concept_id  BIGINT          NOT NULL REFERENCES payroll_concepts(id),
    amount_clp  NUMERIC(18,2)   NOT NULL,
    notes       TEXT,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payroll_items_period_id ON payroll_items(period_id);
CREATE INDEX IF NOT EXISTS idx_payroll_items_concept_id ON payroll_items(concept_id);

-- ============================================================
-- 4. Analytics Vista Materializada
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_payroll_summary AS
SELECT
    p.id AS period_id,
    p.employer_id,
    p.period_year,
    p.period_month,
    p.payment_date,
    SUM(CASE WHEN c.kind = 'income' AND c.is_taxable THEN i.amount_clp ELSE 0 END) AS taxable_income_clp,
    SUM(CASE WHEN c.kind = 'income' THEN i.amount_clp ELSE 0 END) AS gross_income_clp,
    SUM(CASE WHEN c.kind = 'discount' THEN i.amount_clp ELSE 0 END) AS total_discounts_clp,
    SUM(CASE WHEN c.kind = 'income' THEN i.amount_clp ELSE 0 END) -
    SUM(CASE WHEN c.kind = 'discount' THEN i.amount_clp ELSE 0 END) AS net_pay_clp
FROM payroll_periods p
JOIN payroll_items i ON i.period_id = p.id
JOIN payroll_concepts c ON c.id = i.concept_id
GROUP BY p.id;
