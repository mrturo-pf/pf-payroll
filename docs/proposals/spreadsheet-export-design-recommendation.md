# Response: Spreadsheet Export + Blank Template Design

Response to `spreadsheet-export-design-brief.md`. Grounded in the actual code
(`CONCEPT_MAP`, `PayrollRepository`, the `PAY_CONCEPT` reference seed, and the existing
test fixtures) rather than assumptions.

## Route naming: confirmed, with one addition

The brief's proposal holds up:

- `GET /payroll/spreadsheet` — bulk export (download direction, mirroring
  `POST /payroll/import/spreadsheet`'s own name).
- `GET /payroll/spreadsheet/template` — blank template, nested under the same resource.

Both already return JSON-shaped precedent for "format in the noun, not the verb"
(`GET /payroll/{period_id}`, `GET /payroll/summary` never say `export`). No reason to
deviate to `/payroll/export`.

## Item 1 — Inverting `CONCEPT_MAP`, and what doesn't survive the round trip

`CONCEPT_MAP` has 18 entries. Cross-checking every `concept_code` a persisted period can
actually carry against those 18 entries, **exactly two computed-only codes have no
column to export into**:

| `concept_code` | kind | is_taxable | Where it comes from |
| --- | --- | --- | --- |
| `INCOME_TAX` | discount | `FALSE` | `ComputeIncomeTax` use case, saved via `save_computed_income_tax()` |
| `UNEMPLOYMENT_INSURANCE` | discount | `FALSE` | `ComputeUnemploymentInsurance` use case, saved via `save_computed_unemployment()` |

(Confirmed against `pf-db/db/02_seed_base.sql`'s `PAY_CONCEPT` seed — both rows are
`kind='discount', is_taxable=FALSE`, same shape as every other discount concept already
in `CONCEPT_MAP`.)

Everything else a period can carry — `PENSION_BASE`, `PENSION_ADDITIONAL`,
`HEALTH_BASE`, `HEALTH_ADDITIONAL_UF` (the `health_plan_additional` column), and every
income/discount concept a caller can declare — already has a `CONCEPT_MAP` column, even
though some of those are also filled in by `ComputeContributions` rather than declared
by the caller. So the "computed vs. declared" line does not cleanly match the
"has a column vs. doesn't" line — only `INCOME_TAX` and `UNEMPLOYMENT_INSURANCE` are
genuinely columnless today.

**Decision: export them as two extra columns, `income_tax` and
`unemployment_insurance`, appended after the existing `CONCEPT_MAP`-derived columns.**
Reasoning:

- Dropping them silently would make the export lossy for any period that has gone
  through `compute-tax`/`compute-contributions` — most persisted periods. That fails the
  "exporting a period should let you fully represent it" expectation implicitly, even
  though it doesn't break the round-trip test itself (see below).
- On re-import, **the importer must ignore both columns** rather than reject the file.
  `to_long_format()` only ever reads columns present in `CONCEPT_MAP`; an unknown extra
  column is already silently ignored by `row.get(col)` lookups keyed off
  `CONCEPT_MAP.items()`, not off the DataFrame's own columns. So "ignore" is not new
  behavior to add — it already happens. This must be **stated explicitly in
  `docs/api.md`**, though: a caller who fills in `income_tax` on a blank template and
  expects it to persist will otherwise be silently surprised. `INCOME_TAX` and
  `UNEMPLOYMENT_INSURANCE` remain computed exclusively via their own
  `POST /{period_id}/compute-tax` / contribution endpoints — the spreadsheet path is not
  becoming a second way to set them.
- This also means the **round-trip test is unaffected**: reimporting an exported file
  ignores the two extra columns, `import_rows()` never touches those two concepts, and
  the existing `INCOME_TAX`/`UNEMPLOYMENT_INSURANCE` items already persisted for that
  period are left alone (only `import_rows()`'s own delete-then-reinsert of *imported*
  concepts happens — it does not touch concepts it doesn't know about). Confirm this
  with an explicit test case (see Item 5).
- Period metadata with no `CONCEPT_MAP` column (`status`, `reviewed`,
  `pension_plan_id`, `health_plan_id`) is **out of scope for this feature** — none of it
  is declared via the wide import format today either (`status` is derived from whether
  `net_pay` is present, not read as its own column; plan IDs are set via
  `/assign-plans`). Exporting it as informational-only extra columns (never re-imported)
  would be scope creep beyond what the import side already models. Not proposing it now
  — can be a follow-up if a real need shows up (YAGNI).

## Item 2 — Blank template endpoint

`GET /payroll/spreadsheet/template?format=csv|xlsx` (flag-based, see Item 3's format
discussion — a nested path plus a flag would be redundant with two ways to spell the
same choice).

- **Zero data rows.** An illustrative example row invites someone to overwrite it
  incorrectly (leave one field stale) rather than fully understanding each column;
  a header-only file with no ambiguity about what's "sample" vs. "required" is safer for
  a document a human fills in by hand.
- **Header row is the literal `CONCEPT_MAP` keys, plus the fixed non-concept columns**
  (`period_month`, `period_year`, `employer`, `payment_date`, `worked_days`,
  `employment_contract_kind`, `net_pay`) **in the same order the real export uses.**
  Concretely: both the template writer and the real exporter must call the *same*
  function (e.g. `wide_columns() -> list[str]`, derived once from `CONCEPT_MAP.keys()`
  plus the fixed columns) to get their header row — never two independently maintained
  lists.
- Needs no `PayrollRepository` call, no DB round-trip. Independently shippable before
  the real export exists.
- Does **not** include `income_tax` / `unemployment_insurance` — those only ever appear
  on a real export of an already-computed period, never on a blank form meant to be
  filled in for a brand-new import (consistent with "the importer ignores them anyway").

## Item 3 — Endpoint design

**Format selection: `?format=csv|xlsx` query parameter**, default `csv` if omitted.
Reasoning against the alternatives:

- A `.csv`/`.xlsx` path suffix reads fine for a single fixed resource, but this endpoint
  already needs query parameters for filters (below) — mixing a format-suffix with
  query-string filters on the same path is inconsistent REST style.
- `Accept` header content negotiation is more "correct" in a REST-purist sense but has
  no precedent anywhere else in this API, and adds friction for a human just trying to
  download a file from a browser URL (no way to set custom headers).
- A query parameter is simple, discoverable, bookmarkable, and doesn't collide with the
  filters below.

**Filters** (bulk export only — the template ignores all of these): `employer`,
`period_year`, `period_month`. No date-range or status filter — neither has a concrete
precedent as a *filter* elsewhere in this API (`GET /payroll/period-range` computes a
window around "today," it doesn't accept a caller-supplied range; `status` isn't
filterable anywhere today). Omitting all periods when no filter matches, or when no
filter is given at all, should return every persisted period — mirroring
`GET /payroll/summary`'s own no-filter "return everything" behavior, so an export with
no query parameters is the closest analog to "export everything" a caller would expect.

Final paths:

```
GET /payroll/spreadsheet?format=csv|xlsx&employer=...&period_year=...&period_month=...
GET /payroll/spreadsheet/template?format=csv|xlsx
```

## Item 4 — Hexagonal placement

```python
# application/ports/spreadsheet_exporter.py
class PayrollSpreadsheetExporter(Protocol):
    def export(self, periods: list[PayrollPeriodDetailDTO]) -> bytes: ...
    def export_template(self) -> bytes: ...

# infrastructure/exporters/xlsx_exporter.py (or csv_exporter.py — one per format,
# same pattern as import's single module handling both via read_payroll_dataframe())
```

**`PayrollRepository` does NOT already expose what's needed — a new method is
required.** Confirmed by reading the port directly: `get_period_detail(period_id)`
fetches exactly one period, and `list_period_summaries()` returns `PayrollSummaryDTO`
(aggregate totals only, no `items: list[PayrollItemDetailDTO]`). Neither can back a
bulk, full-detail export without an N+1 query loop.

Proposed new port method:

```python
async def list_period_details(
    self,
    *,
    employer: str | None = None,
    period_year: int | None = None,
    period_month: int | None = None,
) -> list[PayrollPeriodDetailDTO]:
    """List full period detail (items included) for periods matching the given filters."""
    ...
```

This is a single query with the same joins `get_period_detail()` already does, just
scoped by the optional filters instead of a single `period_id` — no schema change, no
`pf-db` coordination needed.

Use case:

```python
class ExportPayroll:
    def __init__(self, repository: PayrollRepository, exporter: PayrollSpreadsheetExporter):
        ...
    async def execute(self, filters: ExportPayrollFiltersDTO, fmt: str) -> bytes:
        periods = await self._repository.list_period_details(...)
        return self._exporter.export(periods)  # or export_template() for the template route
```

The blank template route does not need `ExportPayroll` or `PayrollRepository` at all —
it calls `exporter.export_template()` directly, confirming Item 2's "independently
shippable" claim.

## Item 5 — Round-trip test plan

Two integration tests, living next to the existing
`tests/integration/api/test_payroll_import.py`
(new file: `tests/integration/api/test_payroll_export.py`, mirroring that module's
existing DB-fixture setup rather than duplicating it):

1. **`test_export_reimport_validate_round_trip`**: seed 2+ periods (reuse
   `tests/fixtures/sample_payroll.csv`'s shape, or a small in-test wide `DataFrame`),
   `GET /payroll/spreadsheet` (both `format=csv` and `format=xlsx`), then `POST` those
   exact bytes to `/payroll/import/spreadsheet` with `mode=validate`. Assert
   `validated: true` and every period's diff fields at `0`.
2. **`test_export_reimport_commit_is_a_noop_upsert`**: same setup, but `mode=commit`.
   Assert the response reports the same period IDs (no new periods created) and that
   `GET /payroll/{period_id}` for each returns byte-identical amounts before and after.
3. **`test_export_ignores_computed_only_columns_on_reimport`**: seed a period, run
   `compute-tax` and `compute-contributions` so `INCOME_TAX`/`UNEMPLOYMENT_INSURANCE`
   exist, export it (now carrying those 2 extra columns per Item 1), re-import with
   `mode=commit`, then assert both computed items are **still present** with their
   **original** amounts (proving the importer left them alone rather than deleting them
   as "no longer declared").

**Staying honest against `CONCEPT_MAP` drift:** test 1 should assert the *count* of
non-empty columns in the exported header row equals `len(CONCEPT_MAP) + len(FIXED_COLUMNS)`
(a direct, no-implementation-knowledge-needed indicator computed from the app's own
`wide_columns()` helper) — the moment someone adds a new key to `CONCEPT_MAP` for import
but the exporter's inverse mapping isn't updated in lockstep, this assertion fails
loudly instead of silently exporting an incomplete file.

## Item 6 — Action plan

**Stage 1 (smallest, ship first): blank template only.**
`GET /payroll/spreadsheet/template`, the shared `wide_columns()` helper, CSV+XLSX
adapters' `export_template()`. No `PayrollRepository` changes. Update `docs/api.md` and
the Postman collection as part of this stage, not after.

**Stage 2: bulk export.** Add `list_period_details()` to `PayrollRepository` (and its
SQLAlchemy implementation), the `ExportPayroll` use case, `export()` on both adapters,
the route with its filters. Update `docs/api.md` / Postman again in the same stage.

**Stage 3: round-trip tests.** Technically could move earlier (test 1 only needs
Stage 2), but keeping it last means it also exercises Stage 1's template header-row
helper being the true single source (test 1's column-count assertion depends on it).

No `pf-db` coordination expected — everything here reads existing tables through the
existing `PayrollRepository` port; confirmed while reading `get_period_detail()` and
`list_period_summaries()` that no schema gap exists, only a missing repository method.

## Worked example

Using `tests/fixtures/sample_payroll.csv`'s single period (ACME, 2026-01), after it has
also gone through `compute-tax` (assume `income_tax=45000`) and
`compute-unemployment` (assume `unemployment_insurance=6000`):

**Real export row** (`GET /payroll/spreadsheet?format=csv`):

```csv
period_month,period_year,employer,payment_date,worked_days,employment_contract_kind,salary_base,monthly_legal_gratuity,teleworking_refund,health_insurance_employer_contribution,vacation_incentive,holiday_bonus,availability_bonus,legal_gratuity_adjustment,prior_salary_difference,pension_base,pension_additional,health_base,health_plan_additional,health_insurance,vacation_bonus_advance,holiday_bonus_advance,salary_advance,prior_month_leave_absence_discount,net_pay,income_tax,unemployment_insurance
1,2026,ACME,2026-01-31,30,indefinite,1000000,250000,50000,10030,,,,,,100000,25000,70000,87500,12000,,,,3000,1105000,45000,6000
```

**Blank template header row** (`GET /payroll/spreadsheet/template?format=csv`) — same
column order, minus `income_tax`/`unemployment_insurance`, zero data rows:

```csv
period_month,period_year,employer,payment_date,worked_days,employment_contract_kind,salary_base,monthly_legal_gratuity,teleworking_refund,health_insurance_employer_contribution,vacation_incentive,holiday_bonus,availability_bonus,legal_gratuity_adjustment,prior_salary_difference,pension_base,pension_additional,health_base,health_plan_additional,health_insurance,vacation_bonus_advance,holiday_bonus_advance,salary_advance,prior_month_leave_absence_discount,net_pay
```

Re-importing the real export row with `mode=validate` ignores `income_tax` and
`unemployment_insurance` (not in `CONCEPT_MAP`) and reconciles the remaining 24 columns
exactly as `sample_payroll.csv` does today — `validated: true`, every diff `0`.

## Recommendation

Build it in the two stages above, always bulk (per the brief's own scope decision),
`?format=csv|xlsx` for format selection, `GET /payroll/spreadsheet` +
`GET /payroll/spreadsheet/template` for the routes. The only genuinely new piece of
domain knowledge this surfaced — `INCOME_TAX` and `UNEMPLOYMENT_INSURANCE` being the
only two columnless computed concepts — is small and mechanical enough to not change
the shape of any of the above; it only decides what 2 extra columns the exporter emits
and confirms the importer already ignores unknown columns for free.

## Cost impact

None. No new dependency (`pandas`/`openpyxl` already installed), no new infrastructure,
no `pf-db` migration. The only new persistent-layer change is one additional read-only
repository method against existing tables.
