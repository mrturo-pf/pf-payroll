## Context

`PAY_PERIOD` currently persists two columns that the user wants to remove:

```sql
-- modules/pf-db/db/01_schema.sql
status                   payroll_status           NOT NULL DEFAULT 'projected',
employment_contract_kind employment_contract_kind NOT NULL DEFAULT 'indefinite',
```

The same fields exist in the `pf-payroll` SQLAlchemy model and flow through DTOs,
repositories, importers, PDF preview, API responses, exports, calculations, CLI commands,
and tests.

This is therefore a schema and workflow redesign, not a simple `DROP COLUMN` change.

## Decisions confirmed by the user

These are fixed requirements for the recommendation and implementation:

1. **Remove the review workflow.** `PAY_PERIOD.status` is not needed. Remove:
   - the `status` column;
   - the `payroll_status` PostgreSQL enum/type;
   - the review use case;
   - `POST /payroll/{period_id}/review`;
   - the `review` CLI command;
   - status from API responses, DTOs, imports, exports, Postman, and documentation.
2. **Replace period-level contract kind with an employment-contract table.** Create a
   new table related to the employer, with a validity interval and a position field.
3. **Derive contract kind from the interval.** The intended rule is:
   - `ended_at IS NULL` → indefinite contract;
   - `ended_at IS NOT NULL` → fixed-term contract.

   The recommendation must formalize this rule and its validation constraints. No
   `employment_contract_kind` enum should survive.
4. **Remove the old field from all API contracts.** It must not remain in request or
   response models, DTOs, Postman examples, CSV/XLSX contracts, PDF preview, or JSON
   import.
5. **CSV/XLSX changes.** Imports must not require `employment_contract_kind`; exports
   must not expose it.
6. **PDF/JSON changes.** PDF preview must not expose `employment_contract_kind`, and
   JSON import must not require or expose it.
7. **Migrate existing data before dropping columns.** Historical period data must be
   migrated into the new contract representation before the destructive schema change.
8. **Drop orphaned enum types.** Once no remaining schema object uses them, remove:

   ```text
   payroll_status
   employment_contract_kind
   ```

The recommendation must not reopen these decisions. It should resolve the migration,
contract-table design, calculation behavior, and remaining implementation blind spots.

## Target contract model

The intended replacement is conceptually:

```text
PAY_EMPLOYMENT_CONTRACT (final name to be recommended)
  id
  employer_id        → PAY_EMPLOYER.id
  started_at         DATE NOT NULL
  ended_at           DATE NULL
  position           VARCHAR/TEXT NOT NULL
```

Payroll calculations resolve the contract applicable to a period using:

```text
contract.employer_id = period.employer_id
contract.started_at <= period.payment_date
contract.ended_at IS NULL OR contract.ended_at >= period.payment_date
```

The calculation layer derives the effective contract kind from the matched interval:

```text
ended_at IS NULL  -> indefinite
ended_at IS NOT NULL -> fixed_term
```

`position` is metadata describing the employee's role during that contract interval. It
must not be confused with the old API `period.timeframe` field (`previous`, `current`,
`future`), which is a temporal position in a query window and is unrelated to employment
position.

## The instruction translated into this codebase's terms

Adapt or remove every dependent flow across the `pf-*` ecosystem:

- `pf-db`: new contract table, constraints, indexes, migration/backfill, downgrade,
  table documentation, and removal of both old enum types.
- `pf-payroll`: ORM model, DTOs, repository queries/commands/imports, contribution and
  unemployment-insurance calculation, API response models, CLI behavior, CSV/XLSX
  import/export, PDF preview, JSON import, and tests.
- `pf-base`: remove the review request from Postman and update any changed payroll
  request/response descriptions.
- `pf-sheets`/`pf-rates`: verify no actual consumer depends on either old field; change
  only if references are found.

## Current consumers that must be adapted

### `status`

Current values:

```text
projected
actual
reviewed
```

Current consumers include:

- `PayrollStatus` ORM enum;
- `PayrollStatusKind` DTO/type definitions;
- period import/read/export DTOs;
- API serialization;
- `ReviewPayrollPeriod` and repository `review_period()`;
- `POST /payroll/{period_id}/review`;
- CLI `review`;
- fixtures, tests, and docs.

The confirmed decision is to remove the workflow rather than replace it. The
recommendation must define what happens to any remaining concepts that currently infer
status from data, especially whether `projected`/`actual` become derived internal
labels, disappear entirely, or remain only as non-persisted presentation values.

### `employment_contract_kind`

Current values:

```text
indefinite
fixed_term
```

Current calculation consumers include:

```python
# payroll/application/services/contribution_computation.py
context.employment_contract_kind

# payroll/application/use_cases/compute_unemployment_insurance.py
context.employment_contract_kind
```

The value affects unemployment-insurance calculation and must be replaced by resolving
the effective contract row for the period. A silent global default is not acceptable:
it could calculate the wrong unemployment insurance for fixed-term employees.

Other current consumers include:

- CSV/XLSX importer input validation and period construction;
- spreadsheet export columns;
- PDF preview inference;
- JSON import DTOs and response models;
- period detail/read models;
- API response schemas;
- reconciliation contexts;
- fixtures and tests.

## Migration requirements

Existing rows must be migrated before the columns are dropped.

The recommendation must specify:

1. **Backfill mapping for historical `employment_contract_kind`:**
   - indefinite period → a contract with `ended_at = NULL`;
   - fixed-term period → a contract with an explicit `ended_at` chosen by a defined
     migration rule.
2. How to avoid creating contradictory or overlapping contract intervals when multiple
   periods for the same employer have different historical values.
3. What `started_at` and `ended_at` values are assigned when only monthly period data is
   available.
4. How the `position` value is populated for historical rows when the old schema has no
   position field.
5. Whether historical fixed-term data needs an explicit “unknown end date” distinction
   instead of incorrectly treating it as indefinite.
6. How previously computed unemployment-insurance amounts remain reproducible after
   the source field is removed.
7. Exact `upgrade()` and `downgrade()` behavior.
8. Deployment ordering so the application and schema never disagree about column
   availability.

## Blind spots introduced by the new contract-table decision

### 1. Contract intervals and overlap rules

The new table needs an explicit rule for overlapping contracts for one employer:

- prohibit overlaps with an exclusion constraint or application validation;
- allow overlaps and select one deterministically;
- support multiple simultaneous positions intentionally.

The recommendation must choose one. A payroll period must never silently match two
contracts and calculate unemployment insurance from an arbitrary row.

### 2. Employer versus employee identity

The proposed relationship is to `PAY_EMPLOYER`, but an employer may have multiple
employees and positions. The current schema appears to model payroll periods at the
employer level rather than with a separate employee/person identity.

The recommendation must confirm whether `employer_id` is genuinely the correct parent
for contracts, or whether the new table needs another identity key before it is created.

### 3. Position is historical data with unknown values

The old schema contains no position field. The migration therefore cannot reconstruct
historical positions automatically unless another source exists. The recommendation
must decide whether historical `position` is:

- nullable for migrated rows;
- populated with an explicit `unknown` value;
- inferred from another existing field;
- omitted from historical rows while required only for new contracts.

### 4. Contract selection date

The matching date is likely `PAY_PERIOD.payment_date`, but the recommendation must
confirm whether the correct date is:

- payment date;
- period month start;
- period month end;
- employment effective date;
- another payroll-specific date.

This affects month-end changes and must be consistent across import, computation, and
recalculation.

### 5. Missing contract behavior

The recommendation must define what happens when a period has no matching contract:

- reject import;
- allow the period but block unemployment-insurance calculation;
- return a pending/warning result;
- use an explicit caller-supplied contract input for one calculation only.

No silent default to indefinite should be introduced without an explicit business
 decision.

### 6. API input strategy for contract data

Because CSV/XLSX, PDF preview, and JSON import must no longer carry
`employment_contract_kind`, the recommendation must explain how contracts are created
or maintained:

- a new contract-management API;
- employer-scoped contract import;
- CLI-only administration;
- seeded/database-only management;
- another approved interface.

Removing the old field without adding a way to create the new contract records would
make new payroll periods impossible to calculate safely.

## Additional repository constraints

- `pf-db` owns the schema and migrations; `pf-payroll` cannot change the ORM alone.
- Migrations must be idempotent and have a real downgrade.
- Financial values remain `Decimal`/PostgreSQL `NUMERIC`.
- The new contract lookup belongs behind a repository/application port, not embedded in
  route handlers.
- All existing API/docs/Postman contracts must be updated in the same implementation
  change.
- The zero-duplication gate, Ruff, mypy, tests, and 100% coverage requirements remain.
- Existing import/export and PDF flows must be tested end-to-end against the new
  contract resolution behavior.

## What I need from the recommendation

The recommendation should provide:

1. Final table name and exact columns for the employment-contract table.
2. Foreign keys, indexes, uniqueness constraints, and overlap rules.
3. Exact contract-selection query for a payroll period.
4. Derived-kind rule and missing-contract behavior.
5. Position nullability and historical backfill policy.
6. Historical migration algorithm for indefinite and fixed-term periods.
7. Treatment of already-computed unemployment-insurance values.
8. Removal plan for `status`, review use case/route/CLI, and status DTO fields.
9. Removal/adaptation plan for `employment_contract_kind` in all API, DTO, import,
   export, PDF, JSON, and CLI contracts.
10. New contract-management interface, if one is required.
11. Exact `pf-db` migration and downgrade sequence.
12. File-by-file impact matrix across `pf-db`, `pf-payroll`, `pf-base`, `pf-rates`, and
    `pf-sheets`.
13. Test and rollout plan with backward-compatibility boundaries.

## Response format

- **Deliverable:** a new Markdown recommendation file in this same folder:
  `docs/proposals/pay-period-columns-removal-recommendation.md`.
- Ground the recommendation in the actual schema and code, especially:
  - `modules/pf-db/db/01_schema.sql`;
  - `modules/pf-db/alembic/versions/0002_payroll_schema.py`;
  - `payroll/infrastructure/db/models/payroll.py`;
  - `payroll/application/services/contribution_computation.py`;
  - `payroll/application/use_cases/compute_unemployment_insurance.py`;
  - `payroll/application/use_cases/process_imported_payroll_periods.py`;
  - `payroll/infrastructure/importers/xlsx_importer.py`;
  - `payroll/infrastructure/pdf_import/extractor.py`;
  - `payroll/interfaces/api/routes/payroll.py`.
- Include a worked migration example for at least one indefinite and one fixed-term
  historical period.
- Distinguish deleting a database column, deleting an HTTP endpoint, deleting a CLI
  command, and deleting a use case. They are separate decisions.
- Do not introduce a silent contract-kind default.

## Stack

- `pf-db`: PostgreSQL schema and Alembic migrations.
- `pf-payroll`: Python/FastAPI, SQLAlchemy, Pydantic, CLI, CSV/XLSX importer/exporter,
  PDF preview/import pipeline.
- `pf-base`: Postman collection synchronization.
- `pf-rates`, `pf-sheets`: expected to require no changes unless actual references are
  found.
