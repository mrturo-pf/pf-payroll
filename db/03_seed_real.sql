-- ============================================================
-- Real operational seed data
-- ============================================================
-- Add non-test operational bootstrap data here.
-- This file is applied only through the explicit real-seed flow.
--
-- 1. Pension institutions
-- ============================================================
INSERT INTO pension_plans (institution_id, valid_from, valid_to, additional_rate) VALUES
    (5, DATE '2024-11-01', NULL, 0.0116);

-- ============================================================
-- 2. Health institutions
-- ============================================================
INSERT INTO health_plans (institution_id, valid_from, valid_to, plan_name, contracted_uf) VALUES
    (6, DATE '2024-11-01', NULL, 'Base', 5.42),
    (6, DATE '2024-11-01', NULL, 'GES', 0.91),
    (6, DATE '2024-11-01', NULL, 'Adicionales', 0.79);

-- ============================================================
-- 3. Contribution caps
-- ============================================================
INSERT INTO contribution_caps (cap_type, valid_from, valid_to, value_uf) VALUES
    ('pension_health', DATE '2024-01-01', DATE '2024-12-31', 84.3000),
    ('pension_health', DATE '2025-01-01', DATE '2025-12-31', 87.8000),
    ('pension_health', DATE '2026-01-01', DATE '2026-01-31', 89.9000),
    ('pension_health', DATE '2026-02-01', NULL, 90.0000)
ON CONFLICT (cap_type, valid_from) DO UPDATE
SET
    valid_to = EXCLUDED.valid_to,
    value_uf = EXCLUDED.value_uf;

-- ============================================================
-- 4. Employers
-- ============================================================

INSERT INTO employers (
    name,
    tax_id,
    country_code,
    started_at,
    payment_date_rule,
    payment_month_offset,
    payment_day_of_month,
    payment_business_day_offset,
    payment_calendar_day_offset,
    payment_effective_on_processing_next_day,
    payment_fixed_day_roll,
    first_increase_period_year,
    first_increase_period_month,
    increase_frequency
) VALUES
    (
        'DALT-CONSULTORES',
        '52.005.257-7',
        'CL',
        DATE '2016-07-18',
        'last_business_day_of_month',
        0,
        NULL,
        0,
        0,
        FALSE,
        'previous_business_day',
        NULL,
        NULL,
        NULL
    ),
    (
        'CLINICA-ALEMANA',
        '77.413.290-2',
        'CL',
        DATE '2018-04-03',
        'calendar_days_before_end_of_month',
        0,
        NULL,
        0,
        7,
        TRUE,
        'previous_business_day',
        NULL,
        NULL,
        6
    ),
    (
        'WALMART-CHILE',
        '76.042.014-K',
        'CL',
        DATE '2024-11-18',
        'last_business_day_of_month',
        0,
        NULL,
        1,
        0,
        TRUE,
        'previous_business_day',
        2026,
        5,
        NULL
    )
ON CONFLICT (name) DO UPDATE
SET
    tax_id = EXCLUDED.tax_id,
    country_code = EXCLUDED.country_code,
    started_at = EXCLUDED.started_at,
    payment_date_rule = EXCLUDED.payment_date_rule,
    payment_month_offset = EXCLUDED.payment_month_offset,
    payment_day_of_month = EXCLUDED.payment_day_of_month,
    payment_business_day_offset = EXCLUDED.payment_business_day_offset,
    payment_calendar_day_offset = EXCLUDED.payment_calendar_day_offset,
    payment_effective_on_processing_next_day = EXCLUDED.payment_effective_on_processing_next_day,
    payment_fixed_day_roll = EXCLUDED.payment_fixed_day_roll;

-- ============================================================
-- 5. Complementary insurance providers
-- ============================================================
INSERT INTO complementary_insurance_providers (name) VALUES
    ('METLIFE')
ON CONFLICT (name) DO NOTHING;

-- ============================================================
-- 6. Complementary insurance plans
-- ============================================================
INSERT INTO complementary_insurance_plans (
    provider_id,
    name,
    cost_type,
    cost_value,
    cost_currency,
    valid_from,
    valid_to
) VALUES
    (
        (SELECT id FROM complementary_insurance_providers WHERE name = 'METLIFE'),
        'SEGURO DENTAL - PLAN AVANZADO',
        'fixed_uf'::complementary_insurance_cost_type,
        0.19,
        'UF',
        DATE '2025-02-01',
        NULL
    ),
    (
        (SELECT id FROM complementary_insurance_providers WHERE name = 'METLIFE'),
        'SEGURO DE SALUD - PLAN DESTACADO',
        'fixed_uf'::complementary_insurance_cost_type,
        0.25,
        'UF',
        DATE '2025-01-01',
        DATE '2025-01-01'
    ),
    (
        (SELECT id FROM complementary_insurance_providers WHERE name = 'METLIFE'),
        'SEGURO DE SALUD - PLAN DESTACADO',
        'fixed_uf'::complementary_insurance_cost_type,
        0.83,
        'UF',
        DATE '2025-02-01',
        NULL
    ),
    (
        (SELECT id FROM complementary_insurance_providers WHERE name = 'METLIFE'),
        'SEGURO CATASTROFICO - PLAN AVANZADO',
        'fixed_uf'::complementary_insurance_cost_type,
        0.13,
        'UF',
        DATE '2025-02-01',
        NULL
    );
