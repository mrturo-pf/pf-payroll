# Payroll workflow

This guide focuses on the payroll business flow endpoints. For the **complete API endpoint inventory**, including market-data and reference-data routes, see [`interfaces.md`](interfaces.md).

## Business flow

```text
import -> assign plans -> compute contributions -> compute tax -> review -> report PDF
```

## 1. Import payroll

API:

```bash
curl -X POST http://127.0.0.1:8000/payroll/import \
  -F "file=@tests/fixtures/sample_payroll.csv"
```

CLI:

```bash
python -m payroll.interfaces.cli.main import-payroll tests/fixtures/sample_payroll.csv
```

## 2. Assign plan snapshots

API:

```bash
curl -X POST http://127.0.0.1:8000/payroll/1/assign-plans \
  -H "Content-Type: application/json" \
  -d '{
    "pension_plan_id": 1,
    "health_plan_id": 1
  }'
```

CLI:

```bash
python -m payroll.interfaces.cli.main assign-plans 1 1 1
```

The assignment step:

- validates that the payroll period exists
- validates that the selected plans exist
- enforces validity against the period payment date
- stores `pension_plan_id` and `health_plan_id` as historical snapshots

## 3. Compute contributions

API:

```bash
curl -X POST http://127.0.0.1:8000/payroll/1/compute-contributions \
  -H "Content-Type: application/json" \
  -d '{
    "pension_plan_id": 1,
    "health_plan_id": 1
  }'
```

CLI:

```bash
python -m payroll.interfaces.cli.main compute-contributions 1 1 1 --uf-value-clp 39000
```

This step:

- reads the imported taxable income
- applies the seeded `pension_health` contribution cap
- computes pension mandatory and additional amounts
- computes health mandatory and additional amounts
- computes unemployment insurance from `employment_contract_kind`
- persists `PENSION_BASE`, `PENSION_ADDITIONAL`, `HEALTH_BASE`, `HEALTH_ADDITIONAL_UF`, and `UNEMPLOYMENT_INSURANCE`

## 4. Compute income tax

API:

```bash
curl -X POST http://127.0.0.1:8000/payroll/1/compute-tax \
  -H "Content-Type: application/json" \
  -d '{}'
```

CLI:

```bash
python -m payroll.interfaces.cli.main compute-tax 1 --utm-value-clp 68000
```

This step:

- reads the taxable payroll income
- subtracts persisted mandatory social-security discounts
- uses the stored or provided UTM value
- resolves the matching tax bracket
- persists `INCOME_TAX`

## 5. Review the period

API:

```bash
curl -X POST http://127.0.0.1:8000/payroll/1/review
```

CLI:

```bash
python -m payroll.interfaces.cli.main review 1
```

Review requires:

- assigned pension and health plans
- computed contribution items
- `INCOME_TAX`

After that, the period status becomes `reviewed`.

## 6. Generate the PDF

API:

```bash
curl -L http://127.0.0.1:8000/payroll/1/report.pdf --output payroll-period-1.pdf
```

CLI:

```bash
python -m payroll.interfaces.cli.main report-pdf 1 --output payroll-period-1.pdf
```

The PDF step requires:

- the payroll period to exist
- the period to already be `reviewed`
- a payroll summary to exist

## 7. Query or deflate results

Period summary and detail:

```bash
curl http://127.0.0.1:8000/payroll/summary
curl http://127.0.0.1:8000/payroll/1
python -m payroll.interfaces.cli.main summary
python -m payroll.interfaces.cli.main period-detail 1
```

Deflation:

```bash
curl -X POST http://127.0.0.1:8000/market-data/refresh \
  -H "Content-Type: application/json" \
  -d '{
    "economic_indices": [
      {
        "code": "IPC_CL",
        "period_year": 2026,
        "period_month": 3,
        "index_value": 113.100000
      }
    ]
  }'

curl -X POST http://127.0.0.1:8000/payroll/1/deflate \
  -H "Content-Type: application/json" \
  -d '{
    "target_year": 2026,
    "target_month": 3,
    "index_code": "IPC_CL"
  }'
```

## Import format

The importer accepts payroll flat files in **`.csv`** and **`.xlsx`** formats with the same column layout.

Minimal CSV:

```csv
period,employer,payment_date,employment_contract_kind,salary_base
Jan/2026,ACME,2026-01-31,indefinite,1000000
```

Full CSV:

```csv
period,employer,payment_date,employment_contract_kind,salary_base,monthly_legal_gratuity,teleworking_refund,health_insurance_employer_contribution,pension_base,pension_additional,health_base,health_plan_additional,health_insurance,prior_month_leave_absence_discount,net_pay
Jan/2026,ACME,2026-01-31,indefinite,1000000,250000,50000,10030,100000,25000,70000,87500,12000,3000,1105000
Feb/2026,ACME,2026-02-28,fixed_term,1000000,250000,50000,10030,100000,25000,70000,87500,12000,3000,1105000
```

Supported payroll amount columns:

- `salary_base`
- `monthly_legal_gratuity`
- `teleworking_refund`
- `health_insurance_employer_contribution`
- `pension_base`
- `pension_additional`
- `health_base`
- `health_plan_additional`
- `health_insurance`
- `prior_month_leave_absence_discount`

Computed concepts are intentionally **not** imported from the CSV:

- `UNEMPLOYMENT_INSURANCE`
- `INCOME_TAX`

Those values are generated later through the workflow steps `compute-contributions`
and `compute-tax`.

Column semantics:

- `health_insurance_employer_contribution` imports an additional **taxable**
  income item, so it affects derived unemployment insurance and income tax.
- `health_insurance` imports an extra discount item for payroll deductions such
  as grouped complementary health-insurance charges; it affects net pay but is
  not treated as a mandatory social-security deduction.
- `prior_month_leave_absence_discount` imports carry-over payroll discounts such
  as prior-month leave or absence adjustments; it affects net pay only.

Import notes:

- `period` must use `Mon/YYYY`, for example `Jan/2026`
- `payment_date` is required
- `employer` is required
- `employment_contract_kind` is required
- accepted contract kind aliases include `indefinite`, `fixed_term`, `indefinido`, and `plazo_fijo`
- `net_pay` is optional; if present, the imported period is marked as `actual`, otherwise it is marked as `projected`
- `net_pay` is **not** imported as a payroll concept row
- when `net_pay` is present, the import response stores the declared value and marks reconciliation as pending until computed contributions and income tax are generated
- during import, the system also tries to fetch any missing `UF`, `UTM`, `USD`, `EUR`, or `IPC_CL` entries required by the imported period so the next calculation steps can run immediately
- all UF-valued payroll contribution calculations use the **last day of the remuneration month**, including contribution caps and `ISAPRE` contracted plan pricing; imports therefore request the `UF` for both the payroll `payment_date` and the month's last day when those dates differ
- if the CSV already includes `PENSION_BASE`, `PENSION_ADDITIONAL`, `HEALTH_BASE`, and `HEALTH_ADDITIONAL_UF`, the import flow automatically computes the remaining modeled concepts that do not depend on plan assignment: `UNEMPLOYMENT_INSURANCE` and `INCOME_TAX`
- once `compute-contributions` and `compute-tax` have completed, the system fills `expected_net_pay_clp`, `net_pay_difference_clp`, and `net_pay_warning` using the fully computed payroll totals; for income tax, the deductible social-security set includes `PENSION_BASE`, `PENSION_ADDITIONAL`, `HEALTH_BASE`, and `UNEMPLOYMENT_INSURANCE`, but excludes `HEALTH_ADDITIONAL_UF`
- each populated payroll amount column becomes one imported payroll concept row for that period
