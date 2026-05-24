-- ============================================================
-- Test-only seed data
-- ============================================================
-- Add non-production fixtures here.
-- This file is applied only through the explicit test-seed flow.
-- No current inserts were moved from db/seed.sql because the existing seed
-- contains production-safe reference and operational bootstrap data.
--
-- 1. Pension institutions
-- ============================================================
INSERT INTO pension_plans (institution_id, valid_from, valid_to, additional_rate)
SELECT pi.id, DATE '2024-01-01', NULL, 0
FROM pension_institutions pi
WHERE NOT EXISTS (
    SELECT 1
    FROM pension_plans pp
    WHERE pp.institution_id = pi.id
      AND pp.valid_from = DATE '2024-01-01'
      AND pp.additional_rate = 0
);

-- ============================================================
-- 2. Health institutions
-- ============================================================
INSERT INTO health_plans (institution_id, valid_from, valid_to, plan_name, contracted_uf)
SELECT hi.id, DATE '2024-01-01', NULL, 'Base', 0
FROM health_institutions hi
WHERE NOT EXISTS (
    SELECT 1
    FROM health_plans hp
    WHERE hp.institution_id = hi.id
      AND hp.valid_from = DATE '2024-01-01'
      AND COALESCE(hp.plan_name, '') = 'Base'
      AND hp.contracted_uf = 0
);
