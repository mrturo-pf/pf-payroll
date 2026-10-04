# Payroll workflow

This guide focuses on the payroll business flow endpoints. For the **complete API endpoint inventory**, including market-data and reference-data routes, see [`api.md`](api.md).

## Business flow

```text
import -> automatic contribution and tax processing -> query results
```

## 1. Import payroll

API:

```bash
curl -X POST http://127.0.0.1:8000/payroll/import/spreadsheet \
  -F "file=@tests/fixtures/sample_payroll.csv"
```


### Alternative: import from a PDF payslip

Instead of a CSV/XLSX file, one or more payslip PDFs can be turned into payroll
periods through a two-step preview/confirm flow. Extraction is template-based
(versioned templates stored in pf-db's `PAY_PDF_TEMPLATE`/`PAY_PDF_TEMPLATE_FIELD`
tables, managed via `POST`/`GET`/`PUT`/`DELETE /payroll/templates*`), never OCR/LLM --
a PDF matching no known template, or containing rows a template doesn't recognize,
still contributes a usable preview with those rows flagged as unresolved
(`concept_code: null`) instead of failing the whole request.

**Step A -- preview (read-only, never persists anything):**

A single file works exactly as before, just wrapped in a one-element JSON array:

```bash
curl -X POST http://127.0.0.1:8000/payroll/pdf-preview \
  -H "X-API-Key: your-api-key-here" \
  -F "files=@payslip.pdf"
```

Or send a batch of distinct liquidaciones in one request by repeating the `files`
field -- the response array preserves upload order, and each PDF is extracted fully
independently (one payslip's template match or rows never affect another's):

```bash
curl -X POST http://127.0.0.1:8000/payroll/pdf-preview \
  -H "X-API-Key: your-api-key-here" \
  -F "files=@payslip-employee-a.pdf" \
  -F "files=@payslip-employee-b.pdf"
```

Before wiring a new employer's template (create it via `POST /payroll/templates` --
see [`docs/api.md`](api.md)), iterate on it through `POST /payroll/pdf-preview`. The
endpoint reads the current active templates from the database and reports which template
matched and which rows remain unresolved. The project no longer provides a CLI template
helper.

**Step B -- confirm the (possibly hand-edited) rows from one or more previews:**

This is meant to be a copy/paste from Step A's own JSON response: wrap the entire
response array (or just the elements you want in this batch, one `PdfImportPreviewResponse`
object per payslip) inside `{"mode": ..., "periods": [...]}` and POST it here as-is --
extra fields
the preview includes that this endpoint doesn't need (`template_id`, and each row's
`raw_label`/`kind`/`confidence`) are silently ignored, not rejected. `periods` must have
at least one element, and no two elements may share the same `(employer, period_year,
period_month)` -- merge their `rows` by hand first if that's genuinely the same period.
Double-check that the employer already has an effective employment contract in
`PAY_EMP_CONT` before importing. The import does not create employment history
implicitly; it fails when no contract covers the payroll payment date.

```bash
curl -X POST http://127.0.0.1:8000/payroll/import/json \
  -H "X-API-Key: your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "validate",
    "periods": [
      {
        "employer": "ACME",
        "period_year": 2026,
        "period_month": 1,
        "payment_date": "2026-01-31",
        "rows": [
          {
            "concept_code": "SALARY_BASE",
            "amount_clp": "1000000"
          }
        ]
      }
    ]
  }'
```

Notice that the header fields (`employer`, `period_year`, `period_month`,
`payment_date`, plus the optional `worked_days`/`declared_net_pay_clp`) are
declared **once per period block**, not per row -- every row within one block comes from
the same single payslip, mirroring `PdfImportPreviewResponse`'s own shape (one set of
header fields, `rows` carrying only `concept_code`/`amount_clp` each). There is
deliberately no
`status` field here either -- it is inferred exactly like the CSV/XLSX importer already
does ("actual" once `declared_net_pay_clp` is known, "projected" otherwise), so it is
never something a caller needs to figure out or pass in.

`mode="validate"` (shown above) runs the exact same pipeline as `mode="commit"` --
contributions, taxes, and net-pay warnings are genuinely computed for every period in the
batch -- but every write is
rolled back at the end, and rows without a resolved `concept_code` are reported back via
`unresolved_rows` (each entry identified by `period_index` + `row_index`) instead of failing the request. Resend the same request with
`"mode": "commit"` once every row is resolved and the preview looks right; `commit`
fails outright (400) if any row still has `concept_code: null`. See
[the PDF import action plan](proposals/pdf-import-action-plan.md) for the full
design/implementation history of this flow, including the pf-rates market-data caching
caveat that applies to both modes.

## 2. Internal calculation services

The internal plan-assignment logic:

- validates that the payroll period exists
- validates that the selected plans exist
- enforces validity against the period payment date
- stores `pension_plan_id` and `health_plan_id` as historical snapshots

### Contribution calculation

The internal contribution-calculation service:

- reads the imported taxable income
- applies the seeded `pension_health` contribution cap
- computes pension mandatory and additional amounts
- computes health mandatory and additional amounts
- resolves the effective employment contract and computes unemployment insurance from its explicit contract kind
- persists `PENSION_BASE`, `PENSION_ADDITIONAL`, `HEALTH_BASE`, `HEALTH_ADDITIONAL_UF`, and `UNEMPLOYMENT_INSURANCE`

### Income-tax calculation

The internal contribution-calculation service:

- reads the taxable payroll income
- subtracts persisted mandatory social-security discounts
- uses the stored or provided UTM value
- resolves the matching tax bracket
- persists `INCOME_TAX`

## 5. Query results


Period summary and detail:

```bash
curl http://127.0.0.1:8000/payroll
curl http://127.0.0.1:8000/payroll/1
```

## Import format

The importer accepts payroll flat files in **`.csv`** and **`.xlsx`** formats with the same column layout.

Minimal CSV:

```csv
period_month,period_year,employer,payment_date,salary_base
1,2026,ACME,2026-01-31,1000000
```

Full CSV:

```csv
period_month,period_year,employer,payment_date,worked_days,pension_plan_id,health_plan_id,salary_base,monthly_legal_gratuity,teleworking_refund,health_insurance_employer_contribution,vacation_incentive,holiday_bonus,availability_bonus,legal_gratuity_adjustment,prior_salary_difference,pension_base,pension_additional,health_base,health_plan_additional,health_insurance,vacation_bonus_advance,holiday_bonus_advance,salary_advance,prior_month_leave_absence_discount,net_pay
1,2026,ACME,2026-01-31,30,1,1,1000000,250000,50000,10030,0,0,45000,0,0,100000,25000,70000,87500,12000,5000,0,15000,3000,1130030
2,2026,ACME,2026-02-28,28,1,1,1000000,250000,50000,10030,0,0,45000,0,15000,100000,25000,70000,87500,12000,0,10000,5000,3000,1150030
```

Supported payroll amount columns:

- `salary_base`
- `monthly_legal_gratuity`
- `teleworking_refund`
- `health_insurance_employer_contribution`
- `vacation_incentive`
- `holiday_bonus`
- `availability_bonus`
- `legal_gratuity_adjustment`
- `prior_salary_difference`
- `pension_base`
- `pension_additional`
- `health_base`
- `health_plan_additional`
- `health_insurance`
- `vacation_bonus_advance`
- `holiday_bonus_advance`
- `salary_advance`
- `prior_month_leave_absence_discount`

Computed concepts are intentionally **not** imported from the CSV:

- `UNEMPLOYMENT_INSURANCE`
- `INCOME_TAX`

Those values are generated by the import post-processing services when their prerequisites are present.

Column semantics:

- `health_insurance_employer_contribution` imports an additional **taxable** income item, so it affects derived unemployment insurance and income tax.
- `vacation_incentive`, `holiday_bonus`, `availability_bonus`, `legal_gratuity_adjustment`, and `prior_salary_difference` import additional **taxable** income items, so they also affect derived unemployment insurance and income tax.
- `health_insurance` imports an extra discount item for payroll deductions such as grouped complementary health-insurance charges; it affects net pay but is not treated as a mandatory social-security deduction.
- `vacation_bonus_advance`, `holiday_bonus_advance`, and `salary_advance` import advance discount items; they affect net pay only and are not treated as mandatory social-security deductions.
- `prior_month_leave_absence_discount` imports carry-over payroll discounts such as prior-month leave or absence adjustments; it affects net pay only.

Import notes:

- `period_month` and `period_year` are required and must be provided together (for example `1` and `2026`)
- `payment_date` is required
- `worked_days` is optional; if omitted, the import defaults to 30
- `pension_plan_id` and `health_plan_id` are optional, but must be provided together
- `health_plan_id` accepts one id or multiple ids separated by commas (for example `2,3`); the informational `contracted_uf`/`contracted_clp` fields sum each plan's `contracted_uf`, prorated by how many days of the period's calendar month that plan's `valid_from`/`valid_to` actually overlaps (a plan valid the whole month contributes its full value; a plan that only starts or ends mid-month contributes a day-weighted fraction). The mandatory-minimum-vs-contracted `additional_amount_clp` discount is computed separately, by splitting the month into sub-periods of constant plan composition and applying the mandatory-minimum comparison once per sub-period rather than once for the whole month -- this matters whenever a mid-month plan change crosses the mandatory-minimum threshold partway through (see `domain/health_plan_proration.py`'s `prorated_additional_amount_clp()` and [`docs/investigations/health-additional-uf-mismatch.md`](investigations/health-additional-uf-mismatch.md), Session 11, for a real example)
- when `pension_plan_id`/`health_plan_id` are omitted, both are deduced from every active reference-data plan overlapping the period's month, not just the plan valid on the 1st -- a plan that only takes effect partway through the month is still assigned and prorated the same way
- `employer` is required and must identify an employer with an effective `PAY_EMP_CONT` contract for `payment_date`
- `net_pay` is optional; if present, the imported period is marked as `actual`, otherwise it is marked as `projected`
- `net_pay` is **not** imported as a payroll concept row
- when `net_pay` is present, the import response stores the declared value and marks reconciliation as pending until computed contributions and income tax are generated
- when the imported period already has pension and health plan snapshots assigned, post-processing also validates `PENSION_BASE`, `PENSION_ADDITIONAL`, `HEALTH_BASE`, and `HEALTH_ADDITIONAL_UF` against the internally computed contribution values
- if plan snapshots are still missing, that contribution validation stays pending until the import post-processing services assign plans and compute contributions
- during import, the system also tries to fetch any missing `UF`, `UTM`, `USD`, `EUR`, or `IPC_CL` entries required by the imported period so the next calculation steps can run immediately
- all UF-valued payroll contribution calculations use the **last day of the remuneration month**, including contribution caps and `ISAPRE` contracted plan pricing; imports therefore request the `UF` for both the payroll `payment_date` and the month's last day when those dates differ
- if the CSV already includes `PENSION_BASE`, `PENSION_ADDITIONAL`, and `HEALTH_BASE`, the import flow automatically computes the remaining modeled concepts that do not depend on plan assignment: `UNEMPLOYMENT_INSURANCE` and `INCOME_TAX` -- `HEALTH_ADDITIONAL_UF` is deliberately not required here: it is the Isapre plan top-up and is genuinely optional (a plan costing exactly the legal base has no such line item at all), so its absence must never block auto-computing the other two concepts
- once `PENSION_BASE`, `PENSION_ADDITIONAL`, `HEALTH_BASE`, `UNEMPLOYMENT_INSURANCE`, and `INCOME_TAX` are all present as items on the period (declared directly or auto-computed above), the system fills `expected_net_pay_clp`, `net_pay_difference_clp`, and `net_pay_warning` using the fully computed payroll totals -- `HEALTH_ADDITIONAL_UF` is excluded from this readiness check for the same reason; for income tax specifically, the deductible social-security set includes `PENSION_BASE`, `PENSION_ADDITIONAL`, `HEALTH_BASE`, and `UNEMPLOYMENT_INSURANCE`, but excludes `HEALTH_ADDITIONAL_UF`
- each populated payroll amount column becomes one imported payroll concept row for that period
