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
-- 3. Employers
-- ============================================================
INSERT INTO employers (name, tax_id, country_code, started_at) VALUES
    ('DALT-CONSULTORES', '52.005.257-7', 'CL', DATE '2016-07-18'),
    ('CLINICA-ALEMANA', '77.413.290-2', 'CL', DATE '2018-04-03'),
    ('WALMART-CHILE', '76.042.014-K', 'CL', DATE '2024-11-18')
ON CONFLICT (name) DO UPDATE
SET
    tax_id = EXCLUDED.tax_id,
    country_code = EXCLUDED.country_code,
    started_at = EXCLUDED.started_at;
