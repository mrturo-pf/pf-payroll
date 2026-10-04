## Executive summary

It's feasible and **low-risk** to build the PDF import flow by reusing 100% of the
reconciliation that already exists (a new `from_rows()` + `ProcessImportedPayrollPeriods`
untouched). One-line recommendation: **template-based extraction for the MVP** (cheap,
there's already a real case to build it from), **new routes** instead of overloading
`/payroll/import`, and **no catch-all concepts** in `pf-db` — the real gaps I found are
solved with templates + explicit rejection at `commit`.

## 0. Additional finding: period semantics (outside the scope of the PDF import)

While comparing the period declared by the PDF ("August") against the database, I found
a real convention inconsistency, **independent of the PDF import feature**, which the
user decided to fix:

- **Current convention in historical data:** `period_year`/`period_month` represents
  "the month in which I'll be able to spend that money" — a payment falling at the end
  of a month was recorded under the FOLLOWING month's period.
- **Newly agreed convention:** `period_year`/`period_month` represents **the month
  worked**, exactly as the official document states it (the August PDF is August,
  period). It's simpler, it's what the PDF itself already uses, and it's what the data
  model already defaults to for new employers (`payment_month_offset=0` when creating an
  `EmployerModel` during import).

1. **Code:** the importer (`payroll_repository_imports.py`) currently takes
   `period_year`/`period_month`/`payment_date` from the row as-is, without cross-
   checking them against the employer's configuration (`payment_date_rule` /
   `payment_month_offset`). It's missing an explicit validation that rejects
   (`PayrollValidationError`) an import where the `payment_date` month doesn't match
   what the employer's `payment_month_offset` implies for that period — so the old
   convention can't silently sneak back in.
2. **Data:** historical periods already imported under the old convention for the
   affected employer need to be recalculated: new `period_year`/`period_month` =
   year/month of `payment_date` (given that employer uses `payment_month_offset=0`).
   This includes refreshing the `PAY_MV_SUMARY` materialized view after the `UPDATE`.

**Scope of the data fix:** it's not just the August period that triggered this
conversation — the review showed that **the entire** history for that employer follows
the same old convention consistently, so the data fix is "all or nothing" for that
employer, not a one-off patch for a single period.

**Important — environment scope:** any data fix must be applied first against the local
development database, and **separately, explicitly, and reviewed**, against the real
database (Neon) once the user authorizes it — there is currently no "push" mechanism
from local to Neon, so that second step requires its own hand-reviewed SQL script, not
an automatic copy.

## 1. Analysis of the attached PDF

I analyzed `secrets/Liquidación_202608.PDF` (Corporative Chile S.A., August payslip).
Real names/national ID (RUT)/amounts were redacted from this document — only labels and
structure matter for the design.

### Fields that are NOT a `concept_code` (period/document metadata)

| Field in the PDF | Where it fits in the current domain |
| --- | --- |
| Employer (name) | `ImportPayrollRowDTO.employer` |
| Worker's national ID (RUT) / name | **There is no field for this in `ImportPayrollRowDTO` — and it must stay that way.** The current domain already avoids persisting worker PII; the PDF extractor should only read it for eventual cross-validation (e.g. confirming the PDF matches the expected employee) but **never** include it in the preview JSON nor in what's persisted. |
| Period month / year | `period_year`, `period_month` |
| Worked days | `worked_days` |
| Workplace, department, seniority | No field today — out of scope, not extracted |
| Payment method, account number, bank | No field today and no use case — **do not extract** |
| Totals (income / discounts / net pay) | `declared_net_pay_clp` (the declared net pay); the rest are derived subtotals, not persisted as their own items |

**Finding about extraction robustness:** the header summary block (`SUELDO
BASE / HORAS EXTRAS / TOTAL IMPONIBLE / LEYES SOCIALES / AFECTO A IMPUESTO / IMPUESTO
UNICO`) has 6 columns, but the values row carries fewer numbers whenever one of them is
zero — plain text extraction risks misaligning a column with the wrong value. This is
concrete evidence (not hypothetical) of why coordinate/anchor-based extraction or an LLM
with a forced schema are preferable to a simple text-to-text approach: they need to
anchor each value to its label, not to its position in a sequence.

### Mapping of detailed items to `concept_code`

| Label in the PDF | Type | Proposed `concept_code` | Confidence | Note |
| --- | --- | --- | --- | --- |
| SUELDO | income | `SALARY_BASE` | High | Matches "SUELDO BASE" from the header |
| GRATIFICACION LEGAL | income | `LEGAL_GRATUITY` | High | Name matches exactly |
| ASIGNACIÓN TRAB. HIBRIDO | income | `TELEWORK_REFUND` | Medium | "Hybrid work allowance" ≈ telework refund; non-taxable, matches `is_taxable=FALSE` |
| APORTE SEGURO DE SALUD | income | `HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION` | Medium-High | "Aporte" = employer contribution |
| IMPUESTO | discount | `INCOME_TAX` | High | Matches "IMPUESTO UNICO" from the header |
| COT. SEG. CES. AFP | discount | `UNEMPLOYMENT_INSURANCE` | High | "Unemployment Insurance Contribution" |
| ESENCIAL LEGAL | discount | `HEALTH_BASE` | Medium | Isapre plan name + "Legal" = mandatory bracket (7%) |
| **COMISIÓN AFP** | discount | `HEALTH_ADDITIONAL_UF` | High (confirmed) | User correction: bucketed together with the additional health charge |
| FONDO RETIRO AFP | discount | `PENSION_BASE` (tentative) | Medium | Amount is consistent with the mandatory 10%, but the label doesn't say so explicitly — requires human confirmation in the template |
| ESENCIAL ADICIONAL | discount | `HEALTH_ADDITIONAL_UF` | Medium | Symmetric with "Esencial Legal" / `HEALTH_BASE` |
| **SEGURO DENTAL** | discount | `HEALTH_INSURANCE` | High (confirmed) | User correction: consolidated together with the other two insurances |
| SEGURO DE SALUD | discount | `HEALTH_INSURANCE` | High | Name matches exactly |
| **SEGURO CATASTROFICO** | discount | `HEALTH_INSURANCE` | High (confirmed) | User correction: consolidated together with the other two insurances |

**Important correction to my original analysis:** I had assumed a `concept_code`
couldn't repeat within the same period (that's why I flagged dental/catastrophic as "no
match", to avoid "colliding" with `HEALTH_INSURANCE` already used by Health Insurance).
That assumption was **incorrect** — there is no uniqueness constraint between `PAY_ITEM`
and `PAY_CONCEPT` (the `concept_id` FK is a plain FK, with no `UNIQUE` per period), so
**several items on the same payslip can share the same `concept_code`** without issue.
With this correction, **the analyzed PDF no longer has any real unresolved gaps** — the
3 cases I had flagged as "no match" are now covered.

## 2. Design of the two endpoints

### Endpoint 1 — `POST /payroll/import/pdf-preview`

- Request: `multipart/form-data`, same pattern as `POST /payroll/import` (`UploadFile`).
- Response (`PdfImportPreviewResponse`): `employer`, `period_year`, `period_month`,
  `worked_days`, `declared_net_pay_clp`, and `rows: list[PdfImportPreviewRowDTO]` where
  each row carries `raw_label`, `extracted_amount_clp`, `kind` (inferred
  `income`/`discount`), `concept_code: str | None`, `confidence: float`.
- **Does not inject `PayrollRepository` nor `ProcessImportedPayrollPeriods`** — it's a
  pure `bytes -> DTO` function, with no database I/O. It never raises a 500 for an
  unrecognized PDF: in the worst case it returns rows with `concept_code=null` and
  `confidence=0.0`.

### Endpoint 2 — `POST /payroll/import/rows`

- Request: JSON `{ "mode": "validate" | "commit", "rows": list[ImportPayrollRowDTO] }`.
- New sibling method on the use case: `ImportPayroll.from_rows(rows)` — calls
  `self._repository.import_rows(rows)` directly, without going through any
  `PayrollImporter` (identical to `from_bytes()` minus the parsing step).
- Delegates 100% to `from_rows()` + `ProcessImportedPayrollPeriods.execute()` — the exact
  same sequence already used by `/payroll/import` today.
- Transaction mechanism: wrap both use cases in an explicit `AsyncSession` transaction;
  `commit` does `session.commit()`, `validate` does `session.rollback()` at the end,
  returning the same result in both cases (warnings, diffs, totals).

### Reuse `/payroll/import` or new routes?

**I recommend new routes**, instead of overloading `/payroll/import` with a conditional
content-type (multipart vs JSON) plus a `mode` parameter. Each endpoint keeps a single
responsibility, a single request/response schema in OpenAPI, and `/payroll/import`
(CSV/XLSX) stays completely untouched — zero risk of breaking the existing flow.

## 3. Handling unmatched concepts

**Note after the section 1 correction:** the analyzed PDF no longer has any real gaps
(the 3 cases that looked unmatched were actually valid concepts, once the assumption
that a `concept_code` couldn't repeat within the same period was corrected). I'm keeping
this section anyway — the mechanism is still needed for the next employer/format that
brings a genuinely new concept.

**Confirmed requirement — several items, same concept:** the extractor and endpoint 2
must support more than one payslip item mapping to the same `concept_code` (e.g. Health
Insurance + Dental Insurance + Catastrophic Insurance → all three under
`HEALTH_INSURANCE` as separate rows). This requires no schema change: `PAY_ITEM.concept_id`
is a plain FK with no `UNIQUE` constraint per period, so the data model already supports
this today — the only care needed is that the template (section 5) can declare the same
`concept_code` for several distinct `pdf_label_pattern`s, instead of assuming a 1-to-1
label↔concept relationship.

| Option | Pros | Cons | Impact on `validate`/`commit` |
| --- | --- | --- | --- |
| (a) Seeded catch-all in pf-db (`OTHER_INCOME`/`OTHER_DISCOUNT`) | Guarantees the import never gets blocked | Destroys audit precision (a lumped, undifferentiated bucket can't be reconciled against pf-rates); mandatory cross-repo coordination; risk of silently "guessing" if auto-assigned without a human | N/A — avoids the problem instead of solving it |
| (b) Manual mapping per employer template, with an "unresolved" fallback | Precise; respects the deliberately closed catalog; deliberate, human-driven growth | Initial setup cost per new employer/format | None direct — it's the extraction layer, not the persistence layer |
| (c) Row without `concept_code` in the preview; endpoint 2 rejects it if it arrives unresolved | Zero schema changes; forces an explicit human decision; composes with (b) | Doesn't persist the amount until someone resolves it | `commit` **must fail** (`PayrollValidationError`) if any `concept_code=null` remains; `validate` **does not fail** — it returns a warning with the detail so the user can decide before confirming |

**Recommendation: (b) + (c) together, never (a).** If, over time, the same unmatched
concept (e.g. dental insurance) shows up repeatedly across several employers, that's
when it's justified to add a concept **with its own proper name** via a pf-db migration
— never a generic catch-all.

## 4. Extraction options (endpoint 1 only)

| Method | Accuracy | Tolerance to format changes | Estimated monthly cost | Complexity | Testing without real data |
| --- | --- | --- | --- | --- | --- |
| Template (anchors/regex) | High for the same format; ~0% if it changes without notice | Low | **$0** — local CPU only | Medium (one template per employer+version) | Trivial: synthetic fixtures with the same layout |
| LLM with forced JSON schema (AI Innovation Lab) | High, robust to order/format changes | High | Variable per document — **get a quote from AI Innovation Lab before committing**, don't make up a figure here | Low-Medium (prompt + schema validation) | Mock the provider with recorded JSON responses, no real calls in CI |
| OCR (scanned PDFs) | Medium, depends on scan quality | Low-Medium | $0 (Tesseract) vs. pay-per-page (Document AI/Cloud Vision) | High (image preprocessing + downstream extraction) | Complex — requires image fixtures |
| Hybrid (template first, LLM as fallback) | High in both the common and the rare case | High | Minimal — LLM is only invoked when the template fails | High (two pipelines + decision logic) | Combines the two above separately |

**Recommendation:** start the MVP with **template only** (there's already a real
Corporative Chile case to build it from) and add the LLM fallback only once a second
real employer/format shows up. Starting with an LLM from day 1, without having tried a
template, would be expensive over-engineering — it goes against the ecosystem rule that
the cheapest option wins unless explicitly justified.

## 5. Versionable template system — concrete flow

### Format and location

Plain JSON (no new parsing dependency), one file per version, in
`pf-payroll/infrastructure/pdf_import/templates/<employer_slug>/v<N>.json`. None of this
lives in the database on purpose (YAGNI): these are git-versioned files, so the history
of every mapping change lives in the repo's commit log, not in a mutable, unaudited
table.

Real example (with the section 1 corrections already applied — note how **a single
pattern can match several PDF labels and map to the same concept**, covering the
several-items-to-one-concept requirement):

```json
{
  "template_id": "corporative-chile-v1",
  "employer_match": { "name_pattern": "(?i)corporative chile" },
  "fields": [
    { "pdf_label_pattern": "(?i)^SUELDO$", "concept_code": "SALARY_BASE", "kind": "income" },
    { "pdf_label_pattern": "(?i)GRATIFICACION LEGAL", "concept_code": "LEGAL_GRATUITY", "kind": "income" },
    { "pdf_label_pattern": "(?i)SEGURO (DE SALUD|DENTAL|CATASTROFICO)", "concept_code": "HEALTH_INSURANCE", "kind": "discount" },
    { "pdf_label_pattern": "(?i)COMISI[OÓ]N AFP", "concept_code": "HEALTH_ADDITIONAL_UF", "kind": "discount" }
  ]
}
```

### Step-by-step flow

1. **Cold start (new employer/format, zero templates):** a PDF arrives that matches no
   existing template. Endpoint 1 still responds 200 — never 500 — with every row as
   `concept_code: null`, `confidence: 0.0`. There's no magic here: a human opens the PDF
   next to the preview JSON and builds the first template by hand.
2. **Auto-detection on subsequent imports:** when a new PDF arrives, the extractor tries
   each template against the extracted text and computes a score = number of
   `pdf_label_pattern`s that match at least one line of the detail. The template with
   the highest score is used if it clears a minimum threshold (e.g. ≥ 3 matches); if
   none clears it, it falls back to the point-1 behavior (empty preview, not an error).
3. **Applying the winning template:** each line in the PDF's income/discount detail is
   compared, in order, against the chosen template's `pdf_label_pattern`s. The first one
   that matches sets `concept_code` and `kind`; if none matches, that specific row stays
   `concept_code: null` (even if the rest of the PDF did recognize a template) — this is
   what triggers section 3 (unresolved row).
4. **Adjusting without writing code:** a helper CLI command (`payroll template test <pdf>
   --employer corporative-chile`) runs text extraction + tries the existing templates
   and lists which rows of the PDF were left unmatched. A human edits the JSON by hand
   (adds or adjusts a `pdf_label_pattern`) and reruns the command until it reports 0
   unresolved rows.
5. **New version (the same employer redesigns their PDF):** the signal is that,
   suddenly, the CLI reports many unmatched rows even though there was a template that
   used to work fine. That's when `v2.json` gets created (copying `v1.json` as a base)
   and adjusted — **`v1.json` is never edited in place** once it's already been used, so
   reprocessing an old PDF always gives the same result.
6. **Who maintains this:** it's a deliberately manual, human process (matches
   recommendation (b) from section 3: mapping growth by explicit decision, never
   automatic/silent). There is no job or process that generates or edits templates on
   its own — there could, in the future, be an LLM fallback (section 4) for the case
   where no template matches, but that's a separate layer, it doesn't replace this
   manual per-employer curation flow.

## 6. Testing the validate/commit mode

- **No residue:** use `testcontainers[postgres]` (already an existing dev dependency).
  Run `mode=validate`, count `PAY_ITEM` rows before/after (must be equal), run
  `mode=commit` with the same rows, count again (must go up).
- **Mocking pf-rates:** `MarketDataRepository` is already a `Protocol` — reuse the same
  fake that already makes 100% coverage of `ProcessImportedPayrollPeriods` possible
  (DRY, don't create a new one).

## 7. Action plan

0. **Stage 0 (prior to, and independent of, the PDF import):** fix the period semantics
   per the section 0 finding — (a) add the `payment_date` vs. `payment_month_offset`
   validation in the importer, and (b) recalculate `period_year`/`period_month` for the
   affected historical periods from `payment_date`, refreshing `PAY_MV_SUMARY`. First
   locally, then (separately, with explicit authorization) in Neon. **Not implemented
   yet** — pending the user's explicit go-ahead, treated as its own piece of work, not
   tied to the PDF import roadmap.
1. **Stage 1 (MVP):** Endpoint 1 (preview) with template-based extraction, one real
   template (Corporative Chile, based on this PDF). Independently deliverable and
   testable.
2. **Stage 2:** Endpoint 2 in `commit` mode only (`from_rows()` +
   `ProcessImportedPayrollPeriods`, no dry-run yet) — validates the reuse mechanism
   before adding transaction complexity.
3. **Stage 3:** `validate` mode (transactional rollback).

**Risks:** cross-repo coordination with pf-db would only apply if option (a) from
section 3 were chosen — since the explicit recommendation is NOT to take it, the MVP
requires no pf-db migration. The one side effect already documented in the brief
persists: `ProcessImportedPayrollPeriods` may cache market data in pf-rates' database
even in `validate` mode.

## JSON examples

### Preview (endpoint 1) — generic names, no real data

```json
{
  "employer": "ACME_CL",
  "period_year": 2026,
  "period_month": 8,
  "worked_days": 30,
  "declared_net_pay_clp": "1500000.00",
  "rows": [
    {
      "raw_label": "SUELDO",
      "extracted_amount_clp": "1200000.00",
      "kind": "income",
      "concept_code": "SALARY_BASE",
      "confidence": 0.98
    },
    {
      "raw_label": "SEGURO DENTAL",
      "extracted_amount_clp": "8000.00",
      "kind": "discount",
      "concept_code": null,
      "confidence": 0.0
    }
  ]
}
```

### Confirmation (endpoint 2)

```json
{
  "mode": "validate",
  "rows": [
    {
      "employer": "ACME_CL",
      "period_year": 2026,
      "period_month": 8,
      "payment_date": "2026-08-31",
      "status": "actual",
      "employment_contract_kind": "indefinite",
      "concept_code": "SALARY_BASE",
      "amount_clp": "1200000.00",
      "worked_days": 30,
      "declared_net_pay_clp": "1500000.00"
    }
  ]
}
```

## Final recommendation

0. **Period semantics:** adopt "the period represents the month worked" (like the PDF),
   not "the month in which the money can be spent". A code fix (validation in the
   import) + a historical data fix, both pending explicit approval — see Stage 0 of the
   action plan.
1. Extraction: **template first**, LLM (via AI Innovation Lab) only as a fallback in a
   second stage.
2. Unmatched concepts: **templates + explicit rejection at `commit`**, never a catch-all
   in pf-db. The analyzed PDF left no real gaps once several items sharing the same
   `concept_code` was supported, but the mechanism stays in place for the next new
   format/employer.
3. **New routes** (`/payroll/import/pdf-preview`, `/payroll/import/rows`) instead of
   overloading `/payroll/import`.
4. Reuse 100% of `ProcessImportedPayrollPeriods` untouched — the design was already
   prepared for this.
5. A 4-stage plan (0 to 3), with the preview as an independently deliverable MVP and
   Stage 0 treated as separate work, not blocking the rest.
