# Remove `PAY_PERIOD.status` and `employment_contract_kind` — recommendation

> This recommendation answers
> `pay-period-columns-removal-brief.md` after reading the real `pf-db` schema,
> `pf-payroll` ORM/models, calculation services, import/export paths, PDF preview, API,
> CLI, tests, and the current Postman surface.
>
> The user's decisions are treated as closed requirements: remove the persisted review
> workflow; replace period-level contract kind with an employer-linked employment
> contract table; remove the old fields from all API/import/export/PDF/JSON contracts;
> migrate existing data first; and drop both PostgreSQL enum types.

## 1. Executive recommendation

Implement the change in two independently verifiable slices:

### Decisions incorporated from the user's clarification

The following decisions are now part of the recommendation:

- contracts for the same employer **must not overlap**;
- `position` is descriptive metadata and is nullable; it is **not** an employee identity
  and does not permit multiple simultaneous employees under the current schema;
- historical contract dates are initialized from the existing employer relationship
  dates (`PAY_EMPLOYER.started_at` / `PAY_EMPLOYER.ended_at`);
- contract kind is an explicit contract attribute, not inferred from whether `ended_at`
  is currently populated;
- an effective contract is mandatory before payroll calculation; missing contract means
  an error, never a silent default;
- `PAY_EMPLOYER.started_at` / `PAY_EMPLOYER.ended_at` cease to be required source
  fields because contract history becomes the source of truth. The recommendation
  treats them as legacy fields to migrate and remove, not as a second competing date
  model.


Create a new `PAY_EMPLOYMENT_CONTRACT` table owned by `pf-db`:

```text
id
employer_id       -> PAY_EMPLOYER.id
started_at        DATE NOT NULL
ended_at          DATE NULL
is_indefinite     BOOLEAN NOT NULL
position          VARCHAR(120) NULL
```

`is_indefinite` is the authoritative historical contract-kind flag. It is intentionally
independent from `ended_at`:

- `is_indefinite = true`, `ended_at = NULL`: currently active indefinite contract;
- `is_indefinite = true`, `ended_at IS NOT NULL`: an indefinite contract that later ended;
- `is_indefinite = false`, `ended_at IS NOT NULL`: fixed-term contract;
- `is_indefinite = false`, `ended_at = NULL`: invalid for a contract whose end date is
  required to define its fixed term and must be rejected.

Therefore, an ended indefinite contract remains historically indefinite. The application
must never infer kind from the mere presence of `ended_at`.

Use the period's `payment_date` to resolve the applicable contract:

```text
contract.employer_id = period.employer_id
contract.started_at <= period.payment_date
AND (contract.ended_at IS NULL OR contract.ended_at >= period.payment_date)
```

The calculation layer derives an in-memory kind from the explicit boolean:

```text
is_indefinite = true  -> indefinite
is_indefinite = false -> fixed_term
```

Do not infer kind from `ended_at`. An indefinite employment relationship can end, and
its historical contract must remain `indefinite` after `ended_at` is populated.


### Slice B — remove review/status and obsolete contracts

Remove:

- `PAY_PERIOD.status`;
- PostgreSQL type `payroll_status`;
- SQLAlchemy `PayrollStatus`;
- `POST /payroll/{period_id}/review`;
- CLI command `review`;
- review use case/repository command;
- `status` from API/DTO/import/export/PDF/JSON/Postman contracts;
- `employment_contract_kind` from all API/DTO/import/export/PDF/JSON/Postman contracts;
- PostgreSQL type `employment_contract_kind`.

Keep the calculation use cases and CLI commands for assigning plans, computing
contributions, and computing tax. They remain useful inside `pf-payroll`, even though
their HTTP endpoints were previously classified as unused and should not be confused
with this schema change.

## 2. Why this design fits the real schema

`PAY_PERIOD` currently belongs directly to `PAY_EMPLOYER` and has no employee/person
identity. The current schema therefore cannot safely support multiple simultaneous
employees or positions under one employer. A contract table linked to `PAY_EMPLOYER` is
the smallest design consistent with the existing data model.

This recommendation deliberately does **not** invent a new employee entity in this
change. Adding one would be a separate domain redesign with a much larger migration
surface.

The `position` field is included as requested, but it is not used to identify a payroll
period or select a contract. It is descriptive metadata for the role held during the
contract interval.

## 3. New table design

Recommended schema:

```sql
CREATE TABLE "PAY_EMPLOYMENT_CONTRACT" (
    id            BIGSERIAL PRIMARY KEY,
    employer_id   BIGINT NOT NULL REFERENCES "PAY_EMPLOYER"(id),
    started_at    DATE NOT NULL,
    ended_at      DATE,
    is_indefinite BOOLEAN NOT NULL,
    position      VARCHAR(120),
    CHECK (ended_at IS NULL OR ended_at >= started_at),
    CHECK (is_indefinite OR ended_at IS NOT NULL)
);
```

`is_indefinite` is deliberately independent from `ended_at`. A contract can be
indefinite during its life and later receive an `ended_at` when the employment
relationship ends; that historical row must remain `is_indefinite = TRUE`.

### 3.1 Indexes
Add an index optimized for period resolution:

```sql
CREATE INDEX ix_pay_employment_contract_lookup
    ON "PAY_EMPLOYMENT_CONTRACT" (employer_id, started_at, ended_at);
```

The repository query should additionally order by:

```text
started_at DESC, id DESC
```

The ordering is defensive and deterministic if bad historical data ever contains more
than one matching row. The migration should still prevent overlap for new writes.

### 3.2 Overlap policy

Because the current model has no employee identity, prohibit overlapping contracts for
the same employer. Two simultaneous contracts for one employer cannot be assigned to a
period deterministically in this schema.

Recommended enforcement:

- application validation with a transaction-safe overlap check;
- PostgreSQL exclusion constraint using a daterange if the existing database policy
  permits the required `btree_gist` extension;
- otherwise, serialize contract writes and enforce the overlap check in the repository
  transaction.

The recommendation prefers the database exclusion constraint for correctness, but the
implementation must confirm whether enabling `btree_gist` is acceptable in `pf-db`. If
not, use a repository-level transaction lock plus the overlap query. Do not rely only on
an application pre-check with no transaction protection.

### 3.3 Position nullability

`position` should be nullable initially.

Reason: the old `PAY_PERIOD` schema contains no position data, so historical migration
cannot honestly reconstruct it. New contract-management requests may require a position
for newly created contracts once the caller has real data, but historical rows should
carry `NULL`, not a fabricated value such as `"unknown"`.

## 4. Contract resolution behavior

Add a repository/application DTO representing the effective contract without reviving
the old persisted enum:

```python
@dataclass(frozen=True, slots=True)
class EmploymentContractDTO:
    id: int
    employer_id: int
    started_at: date
    ended_at: date | None
    position: str | None
```


Use `PAY_PERIOD.payment_date` as the contract-selection date.

This is the most faithful existing date because:

- it is already required on `PAY_PERIOD`;
- contribution and unemployment calculations operate for the payroll payment;
- import and PDF flows already resolve payment dates;
- it avoids silently choosing month start or month end when payment rules can vary by
  employer.

The rule must be centralized in one repository method, for example:

```text
get_effective_employment_contract(employer_id, payment_date)
```

No calculation service should independently reimplement interval matching.

### 4.2 Missing contract behavior

An effective contract must exist before payroll calculation. Do not silently default a
missing contract to `indefinite`.

Recommended behavior:

- payroll import may parse/stage a period, but the import transaction must fail before
  committing if no effective contract exists for the period;
- explicit contribution/unemployment calculation must fail with a domain/application
  error explaining that an effective employment contract is missing;
- the error must include employer, period, and payment date so the operator can create
  or correct the contract;
- no payroll period may be considered fully processed while its contract lookup fails.

This makes the contract table a true prerequisite instead of a best-effort enrichment.

## 5. Unemployment-insurance calculation

Keep the unemployment-insurance calculation, but replace its input source.

Current path:

```text
PayrollPeriodModel.employment_contract_kind
    -> calculation context
    -> ComputeUnemploymentInsurance
```

Recommended path:

```text
PayrollPeriodModel.employer_id + payment_date
    -> EmploymentContractRepository.get_effective_contract(...)
    -> derived in-memory contract kind
    -> ComputeUnemploymentInsurance
```

The calculation service should depend on a small application port or calculation
context, not on the SQLAlchemy model. The service should not know how contracts are
stored.

### 5.1 Historical reproducibility

Previously computed `UNEMPLOYMENT_INSURANCE` values remain persisted payroll items.
Removing the source column must not rewrite those amounts automatically.

When a historical period is recalculated after migration:

- resolve the migrated employment contract;
- use its explicit `is_indefinite` value;
- calculate the new result;
- surface any migration uncertainty as a warning/error rather than silently changing
  the amount.

The migration must produce a report containing at least:

- periods migrated as indefinite;
- periods migrated as fixed-term;
- periods with no contract match after migration;
- employers whose old period-level values conflict;
- migrated contracts with `position = NULL`.

## 6. Historical data migration

### 6.1 Status migration

Because the user explicitly does not want the review workflow, existing statuses are
not migrated to a replacement table.

Before dropping the column:

- export an audit snapshot if operational history is required outside the application;
- record counts by old value (`projected`, `actual`, `reviewed`) in the migration log;
- accept that `reviewed` state is intentionally discarded from the application model.

No replacement `reviewed` column or table should be created.

### 6.2 Contract migration

The old data contains a contract kind per period but no contract dates or position. The
user approved using the existing employer relationship dates as the historical contract
interval:

```text
started_at = PAY_EMPLOYER.started_at
ended_at   = PAY_EMPLOYER.ended_at
position   = NULL
```

The migration creates one historical contract per employer from those dates. Its
`is_indefinite` value must be determined from the old period data, not from `ended_at`:

- if the employer's historical periods consistently contain `indefinite`, set
  `is_indefinite = TRUE`;
- if they consistently contain `fixed_term`, set `is_indefinite = FALSE`;
- if old periods conflict, the migration must stop that employer for manual resolution
  or create an explicit migration error report. It must not choose a value silently.

This preserves the user's rule that an ended indefinite contract remains indefinite:
`ended_at` represents the end of the relationship, while `is_indefinite` represents the
contract type.

Because the existing model has no employee identity and overlapping contracts are
forbidden per employer, the migration cannot represent multiple simultaneous historical
contract kinds for one employer. Conflicting histories are therefore migration errors,
not rows to be guessed around.

Historical `position` is set to `NULL` because the old schema contains no position data.
That is not a blind spot anymore; it is the explicit approved migration policy.

After backfill succeeds, all employer lifecycle reads must use the contract table. Only
then can the migration drop `PAY_EMPLOYER.started_at` and `PAY_EMPLOYER.ended_at`.

### 6.3 Safer preferred migration mode

The recommendation prefers a two-mode migration:

- **automatic mode** for unambiguous sequences, producing the approximation above;
- **quarantine/report mode** for conflicting or insufficient histories, leaving those
  employers for explicit contract administration before financial recalculation.

The migration must not create overlapping intervals or silently assign an arbitrary
contract.

### 6.3 Automatic migration versus quarantine

These terms describe two different treatments for historical data that cannot be
represented safely:

- **Automatic migration:** the migration creates the new contract row without human
  intervention when the old data provides one unambiguous contract kind for the employer.
  It uses `PAY_EMPLOYER.started_at`/`ended_at`, sets `position = NULL`, and copies the
  unambiguous kind into `is_indefinite`.
- **Quarantine:** the migration does not create a potentially wrong contract for an
  employer whose historical period rows contain conflicting contract kinds. It records
  the employer and its conflicting periods in a migration report/table, marks the
  migration as requiring resolution, and prevents the destructive `DROP COLUMN` phase
  from completing until the conflict is resolved.

Given the user's requirement that a contract must exist before calculation, quarantine
is safer than choosing a default. A quarantined employer cannot complete payroll
calculation until an administrator creates the correct contract. This is not a runtime
"pending" status replacement; it is a one-time data migration safety gate.

The migration should fail closed for conflicts, but remain idempotent: rerunning after
manual corrections must not create duplicate contracts or duplicate quarantine records.


### 7.1 Remove `status`

Remove from:

- payroll period read/detail DTOs;
- import result DTOs;
- HTTP response models;
- `GET /payroll` and `GET /payroll/{period_id}` responses;
- spreadsheet export/template contracts;
- CLI output where applicable;
- Postman descriptions/examples;
- API/workflow documentation.

Remove the route and command:

```text
POST /payroll/{period_id}/review
CLI: review
```

### 7.2 Remove `employment_contract_kind`

Remove from:

- CSV/XLSX importer required columns and validation;
- CSV/XLSX export columns;
- PDF preview response;
- JSON import request/response DTOs;
- payroll period API response models;
- Postman bodies and descriptions;
- fixtures and test payloads;
- docs and examples.

The PDF extractor must stop inferring the value. The JSON import must stop requiring it.
The importer should no longer reject a file because this column is absent.

### 7.3 New contract-management interface

Removing the old input field creates a new operational need: someone must be able to
create/update contracts.

Recommend adding an explicit employer-contract interface in a follow-up slice, ideally:

```text
POST /employers/{employer_id}/contracts
GET  /employers/{employer_id}/contracts
PUT  /employment-contracts/{contract_id}
DELETE /employment-contracts/{contract_id}
```

If employer management is not yet an exposed HTTP surface, provide an equivalent
CLI/admin command first. Do not make contract creation depend on editing the database
manually forever; that would be a spectacularly durable footgun.

## 8. File-by-file impact matrix

### `pf-db`

| File/area | Change |
| --- | --- |
| `db/01_schema.sql` | Add `PAY_EMPLOYMENT_CONTRACT`; remove two `PAY_PERIOD` columns; remove enum types. |
| Alembic migration | Backfill contracts, report ambiguous histories, drop columns/types, real downgrade. |
| `docs/tables.md` | Document new table and remove old fields/types. |
| DB tests/fixtures | Add contract rows and remove old period fields. |

### `pf-payroll`

| File/area | Change |
| --- | --- |
| ORM payroll models | Remove period fields/status enum; add contract model. |
| Application DTOs | Remove status/contract-kind API fields; add contract DTO/derived calculation context. |
| Repository ports | Add effective-contract lookup and contract administration methods. |
| Contribution/unemployment services | Resolve effective contract by employer/payment date. |
| Importer | Stop requiring `employment_contract_kind`. |
| PDF extractor | Stop inferring/exposing contract kind. |
| JSON import | Remove field from request/response contract. |
| Spreadsheet exporter | Remove column. |
| Payroll routes | Remove review route and old fields from responses. |
| CLI | Remove `review`; retain calculation CLI commands; add contract administration if needed. |
| Tests | Update all fixtures and add contract interval/missing/overlap/migration tests. |

### `pf-base`

| File/area | Change |
| --- | --- |
| Postman collection | Remove review request and old status/contract-kind examples; add contract requests if the new API is implemented in this slice. |

### `pf-rates`, `pf-sheets`

No changes expected after repository-wide reference verification.

## 9. Why expand/backfill/contract is recommended

A direct migration that drops the old columns immediately is unsafe because the running
application currently reads them while the new contract table does not yet exist. The
three phases avoid that mismatch:

### Expand

Add the new contract table, indexes, constraints, repository methods, and application
lookup while keeping the old columns temporarily. Deploy code that can read the new
source without requiring the old fields to be removed yet.

### Backfill

Populate contracts from the existing employer dates and unambiguous historical contract
kind. Produce the migration report and quarantine conflicting employers. Adapt payroll
calculations to use the new table and require an effective contract. Verify that all
periods that must be calculable have a matching contract.

### Contract

Only after the application no longer reads the old fields:

- remove `PAY_PERIOD.status`;
- remove `PAY_PERIOD.employment_contract_kind`;
- remove `PAY_EMPLOYER.started_at`;
- remove `PAY_EMPLOYER.ended_at`;
- remove `payroll_status`;
- remove `employment_contract_kind`.

This phase is destructive. Its migration must have a downgrade that recreates the old
columns/types as far as possible from the preserved migration snapshot. A downgrade
cannot reconstruct discarded review history or original contract dates that were never
stored, so the migration must explicitly document those limitations.

The deployment order is therefore:

```text
expand migration
  -> compatible application deployment
  -> backfill/report/validation
  -> contract application deployment
  -> destructive contract migration
```

The old application revision must not receive traffic after the contract phase, because
it will still select the dropped columns.


## 10. Test plan

### Contract domain/repository

- exact contract match on payment date;
- `is_indefinite = true` remains indefinite even when `ended_at` is populated later;
- `is_indefinite = false` requires `ended_at`;
- contract starts on payment date;
- contract ends on payment date;
- missing contract is a hard calculation/import error;
- overlapping contracts for the same employer are rejected;
- invalid `ended_at < started_at` is rejected;
- position is preserved but does not affect kind derivation.

### Migration

- employer dates are copied into the initial contract interval;
- consistent historical kind populates `is_indefinite`;
- conflicting historical kind quarantines the employer;
- migrated position is `NULL`;
- `PAY_EMPLOYER` lifecycle columns can be removed after derived reads are verified;
- downgrade restores the old columns/types where technically possible;
- migration does not alter existing payroll item amounts automatically.

### Import/export/PDF/JSON

- CSV/XLSX succeeds without the old column;
- CSV/XLSX export does not contain the old column;
- PDF preview does not expose it;
- JSON import accepts periods without it;
- API responses do not contain `status` or `employment_contract_kind`;
- review route is absent from OpenAPI;
- CLI `review` is absent;
- contribution/unemployment calculation resolves the contract table.

## 11. Final recommendation

Proceed with the redesign as follows:

- remove `PAY_PERIOD.status` and the review workflow completely;
- add employer-linked employment contracts with `started_at`, nullable `ended_at`,
  explicit `is_indefinite`, and nullable historical `position`;
- migrate historical values using the existing employer dates before dropping the old fields;
- derive contract kind from `is_indefinite`, never from `ended_at`;
- require an effective contract before payroll calculation or import commit;
- remove the old contract-kind field from every API, import, export, PDF, JSON, and
  Postman contract;
- remove both PostgreSQL enum types;
- add a proper contract-management interface rather than forcing manual database edits;
- deploy the schema/application changes in a coordinated expand/backfill/contract
  sequence.

The remaining implementation guard is historical conflict handling: employers whose old
period rows contain both contract kinds must be quarantined until an explicit
`is_indefinite` value is supplied. Historical `position` remains `NULL` by design because
there is no source field from which to reconstruct it.
