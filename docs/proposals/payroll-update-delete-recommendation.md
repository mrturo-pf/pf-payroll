# Payroll Update and Delete API — Recommendation

> This recommendation answers
> [`payroll-update-delete-brief.md`](payroll-update-delete-brief.md)
after reading the current `pf-payroll` routes, DTOs, use cases, repository ports,
SQLAlchemy adapters, transaction scope, models, tests, and API documentation.

> **Historical note:** any CLI references in this document describe the pre-removal
> state of `pf-payroll`; the CLI is not a supported interface.
>

Implement the feature in two coherent parts:

1. **Structured upsert with identity-aware updates:** extend
   `POST /payroll/import/json` with an optional `period_id` inside each period block.
   The server infers the intent from that field:
   - no `period_id` → insert using the existing natural-key behavior;
   - existing `period_id` → update that exact period;
   - unknown `period_id` → `404`, with no fallback to insert.
2. **Transactional deletion:** add both `DELETE /payroll/{period_id}` for one period
   and `DELETE /payroll` with `{ "period_ids": [...] }` for an atomic batch.

Do **not** add an `operation` field. The current `mode` field already has one clear
responsibility — `validate` versus `commit` — and adding a second discriminator would
make a small contract unnecessarily ceremonial. The ID is enough to express the
client's intent, provided the server validates it strictly.

The implementation must preserve these invariants:

- existing JSON import clients that omit `period_id` continue to insert as before;
- an update never changes the target primary key;
- an update never becomes an insert because the ID is missing from the database;
- the complete submitted item set replaces the old item set;
- update validation uses the real existing reconciliation pipeline and rolls back;
- one invalid item in an update or delete batch rolls back the complete request;
- no tax, contribution, or net-pay algorithm is duplicated in the HTTP adapter;
- all owned period data is deleted together;
- `docs/api.md`, OpenAPI, tests, and Postman describe the same surface.

## 2. Why the current importer cannot be reused unchanged

`SqlAlchemyPayrollImportRepository.import_rows()` currently receives a flat
`list[ImportPayrollRowDTO]`, groups rows by:

```text
(employer, period_year, period_month)
```

and then either creates the matching `PAY_PERIOD` or loads the existing period by that
natural key. For an existing period it replaces the header and deletes/reinserts the
period items. That is suitable for the current create/import flow, but it does not carry
the caller's intended database identity.

Adding `period_id` to every `ImportPayrollRowDTO` would technically work, but would repeat
period-level data on every item and blur the boundary between a period command and an
item command. It also makes a mixed JSON batch harder to validate. The recommended
application contract is therefore grouped:

```text
ImportPayrollPeriodCommandDTO
  period_id: int | None
  employer: str
  period_year: int
  period_month: int
  payment_date: date
  worked_days: int
  declared_net_pay_clp: Decimal | None
  rows: list[ImportPayrollRowDTO]
```

Use a separate `from_periods()` application entry point for JSON blocks. Keep
`from_bytes()` and the existing `from_rows()` path for spreadsheet imports. Both paths
may share repository helpers, but the JSON adapter should not flatten away the target ID
before persistence.

This is a small DTO addition with a large safety benefit: the repository can distinguish
an intentional update from an ordinary natural-key import without teaching every row
about period identity.

## 3. Recommended HTTP contracts

### 3.1 Update or insert: `POST /payroll/import/json`

Request shape: use the complete period object described in the brief's
[validation example](payroll-update-delete-brief.md#validate-an-update-without-persisting-it),
with an optional `period_id` field. The abbreviated form is:

```json
{"mode": "validate", "periods": [{"period_id": 481, "...": "complete period object"}]}
```

`period_id` is optional at the Pydantic boundary:

| Request value | Server behavior |
| --- | --- |
| omitted or `null` | Insert using the existing natural-key path. |
| positive and existing | Update the identified period in place. |
| positive and missing | Return `404`; never create by natural key. |
| zero or negative | Reject before any repository mutation. |

The submitted natural identity must match the target row exactly:

```text
period.employer == target.employer.name
period.period_year == target.period_year
period.period_month == target.period_month
```

The update may change only editable fields:

- `payment_date`;
- `worked_days`;
- `declared_net_pay_clp`;
- supported pension/health plan inputs;
- the complete `rows` set.

It must not move a period to another employer, year, or month. Moving a period is a
separate business operation and must not be smuggled into an update by changing the
natural-key fields around an ID.

Mixed batches are valid. Each block is classified independently by its `period_id`,
then the complete request remains atomic. Reject duplicate target IDs and preserve the
existing duplicate natural-key validation for blocks without IDs.

Keep `mode` exactly as it works today:

- `mode="validate"`: execute the actual write/calculation pipeline inside the existing
  savepoint scope, then roll back. A successful response has `saved: null` and
  `periods[].id: null`.
- `mode="commit"`: execute the same pipeline and commit only after all periods pass.
  A successful response has `saved: true` and preserves the existing IDs for updated
  periods.

A missing target ID is an identity error, not a reconciliation error. It should be
translated to `404` and must resolve the transaction as `validate` before the exception
leaves the route.

### 3.2 Single delete: `DELETE /payroll/{period_id}`

Use the existing path parameter style:

```text
DELETE /payroll/481
```

Responses:

- `204 No Content` after successful deletion;
- `404 Not Found` when the period does not exist;
- `409 Conflict` for a real database/reference constraint;
- no response body on success.

This is a hard delete because the current schema has no deletion marker and the brief
contains no retention requirement. Before implementation, confirm with the product owner
that payroll history is allowed to be removed. If audit retention is mandatory, stop and
replace this recommendation with an archive/soft-delete design rather than quietly
adding a `deleted_at` column.

### 3.3 Bulk delete: `DELETE /payroll`

Use the collection route with a JSON body:

```http
DELETE /payroll
Content-Type: application/json
```

```json
{
  "period_ids": [481, 482, 483]
}
```

Contract:

- the list is required and non-empty;
- every ID must be a positive integer;
- duplicate IDs are invalid;
- cap one request at 500 IDs unless a shared project limit is later established;
- lock and verify all IDs before deleting any row;
- if any ID is missing, return `404` containing the missing IDs and delete nothing;
- if any dependency blocks deletion, return `409` and roll back the complete batch;
- successful deletion returns `204 No Content`;
- there is no partial-success response.

`DELETE /payroll` is unambiguous despite `GET /payroll` already existing: the HTTP
method and request body distinguish the operations, and FastAPI can expose both in
OpenAPI. Do not encode batches as `/payroll/481,482,483`; that is less discoverable,
harder to validate, and needlessly couples the public contract to path parsing.

## 4. Application architecture

### 4.1 Ports and DTOs

Add a grouped application DTO for structured period commands. Keep the HTTP Pydantic
models at the interface boundary and map them into this DTO. Do not import FastAPI
models into `application/`.

Recommended port additions:

```python
class PayrollRepository(Protocol):
    async def import_periods(
        self, periods: list[ImportPayrollPeriodCommandDTO]
    ) -> ImportPayrollResultDTO: ...

    async def delete_periods(self, period_ids: list[int]) -> None: ...
```

The existing `import_rows()` remains available for the spreadsheet importer. If a
shared method is preferred internally, keep the public port methods intention-revealing
and delegate to small private repository helpers.

Recommended use-case structure:

```text
ImportPayroll.from_bytes()       -> existing spreadsheet path
ImportPayroll.from_rows()        -> existing flat-row path
ImportPayroll.from_periods()     -> JSON grouped-period path, with optional IDs
ProcessImportedPayrollPeriods    -> unchanged reconciliation/calculation use case
DeletePayrollPeriods             -> one atomic primitive for one or many IDs
```

`DeletePayrollPeriod` is optional as a separate class. A single delete route may call
`DeletePayrollPeriods.execute([period_id])`; avoiding two nearly identical delete
implementations is the DRY choice.

### 4.2 Update repository algorithm

Implement explicit insert/update branches inside the structured-period repository
operation:

1. Resolve all submitted concepts before mutating period rows.
2. Validate duplicate IDs, duplicate natural keys, unresolved concepts, and plan input
   consistency.
3. For each block with an ID, load `PAY_PERIOD` by primary key with
   `SELECT ... FOR UPDATE`.
4. If it does not exist, raise the application not-found error.
5. Verify employer/year/month against the locked row.
6. For each block without an ID, retain the current employer lookup and natural-key
   insert behavior.
7. Create or update the header without changing the primary key for updates.
8. Replace all `PAY_ITEM` rows for the period with the submitted complete item set.
9. Replace health-plan snapshots only under the same rules currently used by imports;
   do not accidentally erase snapshots merely because a JSON block omitted optional
   plan IDs.
10. Return `ImportedPayrollPeriodDTO` values for the resulting periods.
11. Let `ProcessImportedPayrollPeriods.execute()` perform all existing reconciliation
    and calculated-row writes.
12. Leave outer transaction resolution to `TransactionalSessionScope`; repository
    code must not independently commit the request.

The current repository uses per-period logic and summary refresh helpers. Refactor only
as much as needed to share concept lookup, plan resolution, header assignment, and item
replacement. Do not rewrite the calculation services while adding CRUD operations.

### 4.3 Delete repository algorithm

Use one bulk primitive for both routes:

1. Validate the list before opening mutation logic.
2. Load all requested `PAY_PERIOD` rows with row locks.
3. Compute missing IDs and raise one structured not-found error before deleting.
4. Delete owned child rows explicitly unless the schema audit proves a safe database
   cascade. At minimum inspect `PAY_ITEM`, `PAY_PRD_HLTH`, and complementary-insurance
   associations.
5. Delete the `PAY_PERIOD` rows.
6. Refresh the payroll summary projection once after the batch, not once per ID.
7. Resolve the outer transaction only in the route/dependency scope.

Explicit child deletion is preferred over relying on undocumented cascade behavior. If a
proper `ON DELETE CASCADE` is the cheaper and safer schema contract, coordinate it in
`pf-db` with an idempotent migration and real downgrade; do not edit the schema only in
`pf-payroll`.

## 5. Route and dependency wiring

Add dependencies in `interfaces/api/dependencies.py` for the structured update use case
and delete use case/repository, following the existing transactional factory pattern.
The route functions should remain thin:

- parse and validate request shape;
- map request models to DTOs;
- invoke one use case;
- resolve the transaction;
- translate `PayrollError` through the existing API error mapper.

The update route should retain the current unresolved-row and reconciliation error
behavior. In particular, it must not return a successful commit response merely because
an update happened to find an existing period; the normal reconciliation checks still
control commit success.

Register `DELETE /payroll` with the collection routes and keep
`DELETE /payroll/{period_id}` compatible with the existing `GET /payroll/{period_id}`.
Review route ordering for static paths such as `/spreadsheet` and `/templates`, even
though HTTP method separation prevents most direct collisions.

## 6. Error and transaction matrix

| Case | Status | Transaction result |
| --- | --- | --- |
| Insert block without ID | Normal import response | Commit or rollback according to `mode`. |
| Update block with existing ID | Normal import response | Commit or rollback according to `mode`. |
| Update block with unknown ID | 404 | Roll back complete request. |
| ID/natural-key mismatch | 409 | Roll back complete request. |
| Duplicate target ID | 400/422 | No mutation. |
| Unresolved concept | 422 | Roll back complete request. |
| Reconciliation conflict | 422 | Roll back complete request. |
| Single delete, unknown ID | 404 | No mutation. |
| Bulk delete, any unknown ID | 404 | No requested period is deleted. |
| Bulk delete, dependency conflict | 409 | Roll back complete batch. |
| Valid single/bulk delete | 204 | Commit all deletions. |

Use whichever 400/422 distinction matches the existing project's established validation
convention, but make it stable and document it. Never expose a partially successful bulk
delete as a 200 response.

## 7. Testing recommendation

### Update tests

Add route and application tests for:

- legacy JSON insert without `period_id`;
- insert with explicit `period_id: null`;
- update with an existing ID and stable primary key;
- unknown ID returning 404 without creating a natural-key row;
- employer/year/month mismatch returning 409;
- zero, negative, and duplicate IDs;
- mixed insert/update batches;
- complete item replacement, including removal of an old concept;
- optional plan snapshot behavior;
- real recalculation of contributions, unemployment insurance, income tax, and net-pay
  warnings;
- validate-mode rollback of headers, items, snapshots, and summary;
- commit-mode persistence of exactly the intended changes;
- atomic rollback if any block in a multi-period request fails;
- no network dependency: use the existing pf-rates stubs.

The integration test for `validate` should count or snapshot all affected rows before the
request, issue the same payload with `mode="commit"` afterward, and assert both the
absence of validate residue and the presence of the committed update. Do not assert on
PostgreSQL sequence values; the existing validate flow can consume sequence numbers even
when rows are rolled back.

### Delete tests

Add tests for:

- single delete success with `204`;
- bulk delete success for multiple periods with `204`;
- empty, duplicate, non-positive, and over-limit bulk lists;
- bulk delete with one missing ID returning `404` and leaving every target intact;
- removal of the period and all owned child/association rows;
- summary projection refreshed after deletion and only once for a batch;
- dependency failure rolling back the entire batch;
- deleting one period leaving neighboring periods untouched;
- route coexistence with `GET /payroll`, `/spreadsheet`, and `/templates`.

Use only synthetic payroll data. No real payslips, RUTs, salaries, or derived fixtures
may enter git-tracked files.

## 8. Documentation and rollout

Update in the same implementation change:

1. `modules/pf-payroll/docs/api.md` with the optional `period_id` semantics, update
   errors, single delete, bulk delete body, atomicity, and response statuses.
2. `postman/pf-ecosystem.postman_collection.json` with synthetic requests for:
   - JSON insert;
   - JSON validate update;
   - JSON commit update;
   - single delete;
   - bulk delete.
3. Generated OpenAPI and route tests.
4. Any workflow/development documentation that currently presents import as insert-only.
5. `pf-db` migration and schema docs only if the dependency audit shows a cascade or
   index is required.

Recommended delivery order:

### Slice A — identity-aware structured import

- add grouped DTOs and repository port;
- implement ID-based insert/update behavior;
- preserve the current spreadsheet path;
- add route, API, Postman, and transaction tests;
- run the complete project checks.

### Slice B — single and bulk delete

- audit FKs and child ownership;
- implement the shared bulk delete use case;
- expose both DELETE routes;
- refresh the summary projection once per request;
- add rollback/dependency tests and update documentation.

Do not combine an unrelated schema redesign with this feature. Coordinate only the
minimal `pf-db` change required by the dependency audit.

## 9. Cost and operational impact

No new infrastructure is recommended. The feature uses the existing PostgreSQL
connection, SQLAlchemy session, FastAPI routes, and pf-rates integration. Expected
marginal cloud cost is effectively zero beyond a small number of database statements.

The main operational risk is accidental deletion, not compute cost. Mitigations are:

- require explicit IDs;
- reject unknown IDs instead of guessing;
- make bulk deletion all-or-nothing;
- lock and verify before deleting;
- document the hard-delete behavior;
- require confirmation of retention policy before production rollout.

## 10. Final decision

Approve the following contract:

- `POST /payroll/import/json` remains the single structured import endpoint;
- omitted/null `period_id` inserts;
- existing `period_id` updates in place;
- missing `period_id` target returns `404` and never inserts;
- `mode="validate"` and `mode="commit"` retain their current transaction semantics;
- `DELETE /payroll/{period_id}` deletes one period;
- `DELETE /payroll` deletes a validated batch from `{ "period_ids": [...] }`;
- both delete routes share one atomic bulk primitive;
- all operations reuse the existing hexagonal architecture and reconciliation pipeline;
- no `operation` field and no duplicate tax/contribution implementation.

This is the smallest design that gives the API real CRUD behavior without turning the
import route into a magical guessing machine. IDs express intent; the transaction keeps
that intent from becoming a database ghost story.
