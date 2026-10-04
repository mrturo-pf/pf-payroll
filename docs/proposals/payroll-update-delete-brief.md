# Payroll Update and Delete API Design Brief

**Date:** 2026-10-03
**Repository:** `pf-payroll`
**Status:** implementation instruction prepared; implementation pending

## Objective

Extend the existing payroll HTTP API so an already persisted payroll period can be:

1. **updated** with corrected header data and line items; and
2. **deleted** when it was imported by mistake.

The update must reuse the existing structured import pipeline instead of creating a
second calculation path. The proposed client flow is:

```text
GET /payroll/{period_id}
  -> client edits the period payload
POST /payroll/import/json
  -> period_id is present for every period
  -> mode="validate" first (recommended)
  -> mode="commit" after validation succeeds
DELETE /payroll/{period_id}
```

This is a backend contract and implementation brief. It does not authorize a commit,
delete, migration, or push by itself.

## Current context and constraints

The current API already exposes:

- `POST /payroll/import/json` for one or more structured period blocks;
- `mode="validate" | "commit"` over the whole request;
- `ImportPayroll.from_rows()`;
- `ProcessImportedPayrollPeriods.execute()`;
- `TransactionalSessionScope`, whose outer transaction supports real rollback even
  though repository code performs internal `session.commit()` calls;
- `GET /payroll/{period_id}` for the current nested read representation;
- `PayrollRepository.import_rows()`, which currently finds a period by the natural key
  `(employer, period_year, period_month)` and replaces its items.

The current `import_rows()` behavior is effectively an import/upsert by natural key. It
is not sufficient as an update contract because it does not prove that the caller meant
the existing row identified by a particular database ID. The implementation must add
that identity check rather than silently relying on the natural key.

Follow the repository rules in `AGENTS.md`:

- keep the hexagonal dependency direction;
- put orchestration in application use cases, not route functions;
- expose repository operations through `Protocol` ports;
- use `Decimal` for every monetary value;
- use explicit application errors, never `assert` for validation;
- update `docs/api.md` and the root Postman collection in the same change;
- coordinate any schema/migration change with `pf-db`;
- do not commit or push without explicit user instruction.

## Confirmed API design

### 1. Update through `POST /payroll/import/json`

Keep the existing route and infer the operation from `period_id` on each period block.
This keeps the request compact and matches the existing create contract:

- `period_id` omitted/null: insert using the current natural-key behavior;
- `period_id` present and found: update that exact persisted period;
- `period_id` present and not found: return an error; never insert a replacement.

Do not add a separate `operation` field. The presence of `period_id` is the explicit
identity signal, while `mode` continues to mean only transaction behavior (`validate` or
`commit`).

```json
{
  "mode": "validate",
  "periods": [
    {
      "period_id": 481,
      "employer": "Example Employer",
      "period_year": 2026,
      "period_month": 8,
      "payment_date": "2026-08-31",
      "worked_days": 30,
      "declared_net_pay_clp": "1234567",
      "rows": [
        {"concept_code": "SALARY_BASE", "amount_clp": "1500000"},
        {"concept_code": "HEALTH_BASE", "amount_clp": "142500"}
      ]
    }
  ]
}
```

Contract rules:

| Field | Rule |
| --- | --- |
| `mode` | Keep `"validate" | "commit"`, defaulting to `"commit"` for backwards compatibility. A recommended client uses `validate` before `commit`; the final durable update uses `commit`. |
| `period_id` | Add to each period block as `int | null`. Omitted/null means insert. A positive ID means update that exact period; a positive but nonexistent ID is an error, never an insert. |
| `periods` | Must be non-empty. Each block is independently classified by its `period_id`: blocks with IDs update existing periods; blocks without IDs insert. |
| period identity | The database row identified by `period_id` must belong to the submitted `(employer, period_year, period_month)`. A mismatch is a client error/conflict, never an implicit move or create. |
| IDs in one request | No duplicate `period_id` values. Keep the existing duplicate natural-key validation too. |
| unresolved rows | Preserve the current behavior: `concept_code=null` is never committed; validation reports it in the structured error detail. |
| transaction | The entire batch is atomic. One invalid or conflicting period rolls back every period in the request. |

The existing create behavior must remain unchanged:

- a period without `period_id` retains the current natural-key import behavior;
- existing callers that omit `period_id` continue to create/import as today;
- a period with `period_id` is never silently converted into a create if its target is
  missing;
- mixed batches are allowed when blocks without IDs are inserts and blocks with IDs are
  updates.

The update behavior must be strict:

- a present ID must resolve to exactly one existing period;
- it must not allow changing `employer`, `period_year`, or `period_month` through an
  ID-targeted update. If a future product requirement needs period identity changes,
  that should be a separate, explicitly designed operation;
- it replaces the complete submitted item set, including removing old concepts that
  are not present in the new `rows` list;
- it updates the editable period header fields (`payment_date`, `worked_days`, and
  `declared_net_pay_clp`) and the supported plan snapshot inputs according to the same
  rules as the current import flow;
- it reruns the existing reconciliation/calculation pipeline after replacement.

A request with `period_id` and `mode="validate"` must execute the real update
against the transaction/savepoint, run contribution, tax, unemployment, net-pay, and
warning calculations, and then roll back. It must return no durable ID changes. A
successful `mode="commit"` request returns the existing `period_id` values and
`"saved": true`, using the current `ImportPayrollResponse` contract.

### 2. Delete through `DELETE /payroll/{period_id}`

Add:

```text
DELETE /payroll/{period_id}
```

Recommended response:

- `204 No Content` when the period existed and was deleted;
- `404 Not Found` when no period with that ID exists;
- `409 Conflict` only for a genuine database/reference constraint that prevents the
  deletion;
- no soft-delete semantics unless a separate audit/legal-retention requirement is
  introduced. The current schema has no `deleted_at` or deletion-status contract.

Deletion is a physical deletion of the payroll period and its owned dependent data:

- `PAY_ITEM` rows;
- `PAY_PRD_HLTH` health-plan snapshots;
- any other period-owned association discovered during the dependency audit, including
  complementary-insurance associations if they reference the period;
- the period row itself.

The delete use case must be transactional and must refresh/invalidate the same summary
projection used by imports. It must not call the import/reconciliation pipeline: there
is no remaining payroll to calculate. It should live as a focused application use case,
for example `DeletePayrollPeriod`, backed by a narrow repository port method such as
`delete_period(period_id: int) -> None`. The single-delete use case should delegate to
or share the same bulk primitive described below.

The route must be registered so the existing static routes (`/spreadsheet`,
`/templates`, etc.) continue to win over `/{period_id}`. FastAPI method separation means
this does not conflict with the existing `GET /payroll/{period_id}`, but route ordering
must still be reviewed in `main.py`.

### 3. Bulk delete through `DELETE /payroll`

Add a collection-level delete with a JSON body:

```text
DELETE /payroll
Content-Type: application/json
```

Request body:

```json
{
  "period_ids": [481, 482, 483]
}
```

Use the collection route rather than `DELETE /payroll/{period_id}` with a request body.
The single-resource route stays simple, while the collection route makes the batch
intent explicit and avoids inventing a comma-separated path parameter.

Bulk-delete contract:

- `period_ids` is required, non-empty, contains positive integers, and contains no
  duplicates;
- limit the batch to 500 IDs per request unless the project establishes another shared
  limit;
- all IDs are locked and verified before any delete occurs;
- if any ID does not exist, return `404` with the missing IDs and delete nothing;
- if any dependent row prevents deletion, return `409` and roll back the entire batch;
- on success, physically delete every requested period and its owned data, refresh the
  summary projection once, and return `204 No Content`;
- there is no partial-success response and no per-ID `mode`: the batch is atomic.

The application layer should expose a focused bulk operation, for example
`DeletePayrollPeriods.execute(period_ids)`, and the repository port should provide a
bulk method such as `delete_periods(period_ids: list[int]) -> None`. The single-delete
use case may delegate to the same bulk primitive with one ID, so child cleanup, locking,
projection refresh, and error semantics cannot drift between the two endpoints.

The `DELETE /payroll` route must be declared alongside the collection routes and before
any assumptions that a body belongs to `DELETE /payroll/{period_id}`. It does not conflict
with `GET /payroll`; HTTP method and request-body handling are distinct, but generated
OpenAPI and client examples must expose both delete operations.

## Required implementation shape

### Phase 0 — baseline and dependency audit

1. Confirm the current OpenAPI contract for `POST /payroll/import/json` and
   `GET /payroll/{period_id}`.
2. Trace the complete update path:
   `route -> ImportPayroll.from_rows() -> PayrollRepository.import_rows() ->
   ProcessImportedPayrollPeriods -> response`.
3. Inspect every foreign key referencing `PAY_PERIOD`, including period-owned tables
   not represented by an ORM relationship. Do not assume SQL `ON DELETE CASCADE` exists.
4. Search the ecosystem for consumers of `POST /payroll/import/json`, period IDs, and
   period deletion. Update only real consumers; do not break `pf-sheets` or external
   callers silently.
5. Confirm whether the summary object is a materialized view, a table, or refreshed by
   application SQL, and reuse the established refresh helper.

### Phase 1 — DTOs, validation, and ports

In `interfaces/api/routes/payroll.py`:

1. Add `period_id: int | None = None` to `ImportPayrollPeriodRequest`.
2. Validate the ID semantics in one focused helper:
   - omitted/null ID means insert;
   - positive ID means update;
   - positive but nonexistent ID is an error;
   - duplicate IDs are rejected;
   - an ID/natural-key mismatch is rejected after loading the target row.
3. Keep Pydantic validation for shape/range and application errors for database/business
   rules. Do not put repository access in Pydantic validators.
4. Add narrow `delete_period` and `delete_periods` methods to `PayrollRepository` and
   focused delete use cases, following existing dependency factories. The single-item
   operation may delegate to the bulk operation with one ID.

In the application layer:

1. Extend the import command DTO only as far as needed to carry the optional target
   period ID; do not leak FastAPI request models into application code.
2. Prefer a separate command DTO (for example `UpdatePayrollPeriodCommandDTO`) over
   adding optional update-only fields throughout `ImportPayrollRowDTO`.
3. Keep `ImportPayroll.from_rows()` as the shared entry point for structured input, but
   make the target ID explicit at the application boundary. Do not branch on a missing
   target ID deep inside generic persistence code without first validating the request
   semantics.
4. Introduce a focused `UpdatePayrollPeriods` orchestration use case if the current
   import use case becomes responsible for too many modes. It may delegate the shared
   row conversion and the existing `ProcessImportedPayrollPeriods`; it must not copy
   contribution or tax logic.

### Phase 2 — repository update semantics

Refactor the repository so create and update have explicit paths while sharing small
helpers (concept lookup, period-plan resolution, item replacement, reconciliation
preparation):

- `create` keeps the current natural-key behavior;
- `update` loads each requested ID with a row lock (`SELECT ... FOR UPDATE`) inside the
  current transaction;
- it verifies the row exists and its immutable natural-key identity matches the request;
- it updates the header and replaces the complete item set;
- it replaces plan snapshots only when the same supported plan inputs are supplied,
  preserving current import semantics otherwise;
- it returns the same `ImportedPayrollPeriodDTO` shape consumed by
  `ProcessImportedPayrollPeriods`;
- it never commits independently of the outer `TransactionalSessionScope`.

Do not implement update as “delete by natural key, then insert”. That would change the
period ID, weaken references, make concurrent edits unsafe, and make validation responses
misleading.

For deletion:

- acquire the target row with a lock;
- delete all owned children explicitly unless the schema audit proves a safe cascade;
- delete the period;
- refresh the summary projection in the same transaction;
- return a not-found domain/application error when appropriate.

If a database cascade or index is required, create a coordinated `pf-db` migration with
an explicit downgrade and update `pf-payroll` ORM metadata only in the same coordinated
change. Do not edit `pf-db` from this repository.

### Phase 3 — route and transaction behavior

The update route remains `POST /payroll/import/json`; do not create a second JSON import
route with a subtly different reconciliation implementation.

The route should:

1. reject malformed period-ID values;
2. resolve unresolved concepts exactly as the current route does;
3. dispatch each period as an insert when its ID is absent, or an update when its ID is
   present;
4. invoke `ProcessImportedPayrollPeriods` exactly once on the resulting periods;
5. reject genuine reconciliation conflicts exactly as current import does;
6. call `scope.resolve("validate")` on every application error;
7. call `scope.resolve(payload.mode)` only after the complete batch succeeds;
8. return the existing response schema. Do not change `saved`, `validated`, or
   `periods[].id` semantics.

For delete, use the same transactional dependency pattern as import. There is no
`mode` field for delete: a successful request is committed; an exception rolls back.
Do not add a fake `validate` mode that returns success without deleting unless a separate
preview-delete requirement appears.

## Error contract

Use the existing error translation layer and document these cases:

| Situation | Status | Required behavior |
| --- | --- | --- |
| Period ID omitted/null | 200 flow | Insert using the current natural-key path. |
| Period ID present but missing | 404 | No period is changed and no insert is attempted. |
| Period ID and submitted natural key disagree | 409 | No period is changed; report the conflict clearly. |
| Duplicate target ID | 400/422 | Reject the whole batch. |
| Unresolved `concept_code` | 422 | Preserve current structured `unresolved_rows` behavior. |
| Reconciliation conflict | 422 | Preserve current `conflicting_periods` behavior and rollback. |
| Delete missing ID | 404 | No write; bulk responses identify all missing IDs. |
| Delete blocked by a real dependency | 409 | Roll back and expose a stable business error; do not swallow the database error. |

Whether malformed period-ID values map to 400 or FastAPI's 422 should follow existing
project conventions; choose one consistently and test it. The important invariant is
that they never reach a mutating repository call.

## API examples

### Validate an update without persisting it

```json
{
  "mode": "validate",
  "periods": [
    {
      "period_id": 481,
      "employer": "Synthetic Employer",
      "period_year": 2026,
      "period_month": 8,
      "payment_date": "2026-08-31",
      "worked_days": 30,
      "declared_net_pay_clp": "1000000",
      "rows": [
        {"concept_code": "SALARY_BASE", "amount_clp": "1300000"},
        {"concept_code": "HEALTH_BASE", "amount_clp": "123500"}
      ]
    }
  ]
}
```

A successful validation has the existing 200 response shape, `mode="validate"`,
`saved=null`, and `periods[].id=null` because the transaction was rolled back. A
reconciliation or unresolved-concept problem remains a 422, as it is for current JSON
imports.

### Commit the same update

Resend the corrected payload with only:

```json
{"mode": "commit", "periods": [{"period_id": 481, "...": "..."}]}
```

The actual request must contain the complete period object; the abbreviated example is
illustrative only. A successful response keeps `periods[].id=481` and returns
`saved=true`.

### Delete

```text
DELETE /payroll/481
```

Return `204` with an empty body. Do not return the deleted period as if it still existed.

## Testing requirements

### Update tests

Add isolated tests for:

- create requests without `period_id` remain compatible;
- a period with `period_id` is treated as an update;
- update rejects a non-positive, duplicated, or nonexistent ID;
- update rejects a natural-key mismatch;
- update preserves the target ID;
- update replaces removed/changed/added concepts correctly;
- update refreshes contribution, income-tax, unemployment, net-pay, and warning fields;
- multiple updates are atomic when one period fails;
- unresolved rows and reconciliation conflicts preserve the current 422 payload;
- `validate` leaves period headers/items/plan snapshots/summary unchanged;
- `commit` changes them exactly once;
- concurrent targeting cannot silently update a different row.

The integration test for validation must snapshot relevant rows before the request, run
`mode="validate"` with `period_id` present, compare all snapshots afterward, then run the
same request with `mode="commit"` and assert the intended changes and stable ID. Stub
pf-rates through the existing market-data test seam; do not use the network.

### Delete tests

Add tests for:

- deleting an existing period returns 204;
- bulk deleting several existing periods returns 204;
- bulk delete rejects an empty, duplicated, non-positive, or over-limit ID list;
- bulk delete with one unknown ID returns 404 and leaves every requested period intact;
- the period, items, health snapshots, and every owned association are gone;
- the summary projection no longer contains deleted periods and is refreshed once for a batch;
- deleting an unknown ID returns 404;
- a failed child deletion rolls back the entire operation, including a bulk batch;
- deleting one period does not affect neighboring periods;
- static routes and `GET /payroll/{period_id}` remain correct after adding DELETE.

Use synthetic data only. Never commit a real payslip, RUT, salary, or fixture derived
from a real payslip.

## Documentation and ecosystem synchronization

In the same implementation change:

1. Update `modules/pf-payroll/docs/api.md` with:
   - `period_id` semantics for `POST /payroll/import/json` (omitted = insert, present = update);
   - update validation/commit examples and errors;
   - `DELETE /payroll/{period_id}` and `DELETE /payroll` with their 204/404/409 contracts;
   - the all-or-nothing bulk-delete behavior and request body;
2. Update `postman/pf-ecosystem.postman_collection.json` with:
   - a JSON update request using a synthetic period ID;
   - a single delete request and a bulk delete request with synthetic IDs;
   - validate and commit examples where practical.
3. Update workflow/development documentation if it describes import as create-only.
4. Compare the generated OpenAPI with `docs/api.md` and Postman before declaring the
   change complete.
5. If `pf-db` changes, update its migration/table documentation and record the
   cross-repository dependency explicitly.

## Acceptance matrix

| Criterion | Expected result |
| --- | --- |
| Existing create API | Existing JSON import callers continue working without IDs. |
| Explicit update | A present `period_id` targets an existing row and never creates a missing one. |
| Stable identity | An update preserves each target period's primary key. |
| Same business pipeline | Update reuses `from_rows()`/shared import logic and `ProcessImportedPayrollPeriods`; no duplicate tax/contribution logic. |
| Atomic update | A failing period rolls back the full batch. |
| Safe validation | `mode="validate"` leaves pf-payroll data unchanged; pf-rates cache side effects remain documented. |
| Delete | `DELETE /payroll/{period_id}` physically removes one period and `DELETE /payroll` atomically removes a validated batch, or returns a stable conflict. |
| Projection | Summary data is refreshed consistently after update and delete. |
| API documentation | OpenAPI, `docs/api.md`, and Postman agree. |
| Quality | Unit/integration tests, ruff, mypy, dead-code, duplicate-code, and coverage checks pass. |

## Risks and mitigations

### Natural-key ambiguity

The current importer identifies periods by employer/year/month. An ID-targeted update
must verify both identity forms and lock the target. Otherwise a stale client could
overwrite a different row after a correction to its payload.

### Replacing versus patching items

Payroll rows are a complete payslip representation. Treat update as a full replacement
of the submitted item set, not a partial patch. This prevents old concepts from silently
surviving after a user corrects a payslip. If partial updates are later required, design
an explicit PATCH contract with field-presence semantics.

### Reconciliation side effects

`mode="validate"` rolls back pf-payroll writes, but pf-rates can cache missing market
data in its own database. Preserve the existing disclosure; do not promise global
side-effect freedom.

### Hard deletion and auditability

Physical deletion is the simplest behavior consistent with the current schema, but it
removes the payroll's history. Before implementation, confirm there is no legal,
product, or audit-retention requirement. If one exists, stop and redesign around a
soft-delete/archive policy rather than quietly adding `deleted_at` in this feature.

### Database coordination

A missing cascade, summary refresh dependency, or FK discovered during the audit may
require a `pf-db` migration. That migration must be idempotent, reversible, and reviewed
with the schema owner before `pf-payroll` code is changed.

## Final recommendation

Implement update inference from `period_id` on the existing `POST /payroll/import/json`
contract: absent/null means insert; present means update; present but nonexistent means
an error and never an insert. Preserve the current create behavior for callers that omit
IDs. Execute updates through the same transactional import and reconciliation pipeline,
with row locks, identity verification, full item replacement, and stable primary keys.
Offer `mode="validate"` as the safe first step and `mode="commit"` for the durable update
requested by the caller.

Add both deletion routes: `DELETE /payroll/{period_id}` for one period and
`DELETE /payroll` with `{ "period_ids": [...] }` for a validated batch. Both should use
one transactional bulk primitive, explicitly clean dependent rows, refresh the summary
projection once, and return 204 only after the complete operation succeeds. Do not create
a second update pipeline or silently turn an update into a create. Tiny bit of ceremony,
yes; fewer haunted payroll records, also yes.
