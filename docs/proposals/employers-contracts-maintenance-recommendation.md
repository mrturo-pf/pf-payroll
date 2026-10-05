# Employers and Employment Contracts Maintenance Recommendation

**Date:** 2026-10-04
**Status:** approved
**Level:** L
**Scope:** `pf-payroll` employer/contract maintenance API and coordinated `pf-db` integrity changes
**Related artifacts:** [`employers-contracts-maintenance-brief.md`](employers-contracts-maintenance-brief.md), [`employers-contracts-maintenance-plan.md`](employers-contracts-maintenance-plan.md)
**Approved:** 2026-10-04 by Arturo (conversation)
**Superseded-by:** none

## Executive recommendation

Add two resource-oriented maintenance surfaces under `pf-payroll`:

```text
GET/POST   /payroll/employers
DELETE     /payroll/employers
GET/POST   /payroll/contracts
DELETE     /payroll/contracts
```

`POST` on each collection accepts a non-empty array and infers create/update
from `id`:

- omitted/null `id` → create;
- positive existing `id` → update in place;
- positive missing `id` → `404`, never fallback to create;
- multiple creates, multiple updates, and mixed create/update batches are valid;
- the complete request is atomic.

Use separate employer and contract requests. A contract must reference an
already persisted employer ID; creating an employer and a contract that points
to that employer in the same request is deliberately out of scope. This avoids
temporary IDs and keeps the foreign-key contract honest. Clients create the
employer first, then create contracts using the returned stable ID.

Employer names remain unique and human-facing. All relationships across
`PAY_EMPLOYER`, `PAY_EMP_CONT`, payrolls, templates, DTOs, ports, repositories,
and services use the stable `employer_id`; names are never relationship keys.

## Why this design fits the current ecosystem

The models already exist, but there is no maintenance API. The current
architecture already uses:

- FastAPI interface adapters;
- application use cases and repository Protocols;
- SQLAlchemy adapters;
- `PAY_EMPLOYER.id` and `PAY_EMP_CONT.employer_id` as relational identities;
- existing contract lookup logic used by payroll queries and PDF-related flows.

The feature should add focused employer/contract DTOs, ports, use cases, and
routes. It must not put orchestration or deletion guards in route functions and
must not make PDF preview or payroll import implement a second employer/contract
model.

## API contract

### Employers

Recommended request:

```json
{
  "employers": [
    {
      "id": 481,
      "name": "Existing Employer Corrected",
      "tax_id": "synthetic-tax-id",
      "country_code": "CL"
    },
    {
      "name": "New Employer",
      "tax_id": "synthetic-tax-id-2",
      "country_code": "CL"
    }
  ]
}
```

The final request schema may include the existing payment-date and salary-
increase configuration fields. The recommendation is to expose those fields
explicitly rather than accepting an unbounded model dump. `id` is immutable;
`name` may be edited, subject to the existing unique-name database constraint.
Changing a name never changes employer identity or attached records.

`GET /payroll/employers` lists employers. It should support bounded pagination
and optional exact/name search only as a display/filter concern; returned and
submitted relationships remain ID-based.

`DELETE /payroll/employers` accepts a validated list of employer IDs. Before
mutation, load and lock all requested employers and return a structured `409`
when any employer has contracts or payroll periods. A missing ID returns `404`
and deletes nothing. Success returns `204`.

### Employment contracts

Recommended request:

```json
{
  "contracts": [
    {
      "id": 91,
      "employer_id": 481,
      "started_at": "2026-01-01",
      "ended_at": "2026-06-30",
      "is_indefinite": false,
      "position": "Updated position"
    },
    {
      "employer_id": 482,
      "started_at": "2026-07-01",
      "ended_at": null,
      "is_indefinite": true,
      "position": "New position"
    }
  ]
}
```

`started_at` is required. `ended_at` is optional only for an indefinite contract;
otherwise it is required and must be on or after `started_at`. The API must
reject contradictory `is_indefinite`/`ended_at` combinations rather than
silently normalizing them.

`employer_id` is required for creation and update. It must resolve to a real
employer before any contract mutation. An update must not silently move a
contract to an unknown employer.

`GET /payroll/contracts` lists contracts with employer ID/name and supports
filters for `employer_id`, `started_at`, `ended_at`, and effective-month overlap,
with bounded pagination.

`DELETE /payroll/contracts` accepts contract IDs. It locks and verifies every
contract before deleting. If any requested contract is referenced by a payroll
period in its applicable worked-date range, return `409` with the blocking
contract/payroll IDs and delete nothing. Success returns `204`.

## Contract interval semantics

The global non-overlap rule is preserved exactly as requested: no validity
interval may overlap any other interval, even when the contracts belong to
different employers. Use inclusive calendar dates at the API boundary:

```text
[started_at, ended_at]
```

For persistence and PostgreSQL exclusion semantics, represent an inclusive end
as a half-open range ending on `ended_at + 1 day`; an indefinite contract ends at
`infinity`.

This permits adjacent intervals:

```text
Employer A: 2026-08-01 through 2026-08-05
Employer B: 2026-08-06 through 2026-12-31
```

but rejects any shared date. A contract update must exclude its own current row
from the overlap check before applying the proposed interval.

### Database integrity recommendation

Coordinate a `pf-db` migration adding a PostgreSQL exclusion constraint on the
contract validity range. The migration must:

1. audit existing data for overlaps and stop with a clear remediation result if
   conflicts already exist;
2. use an idempotent, hand-written migration;
3. provide a real downgrade;
4. use the appropriate range/index support already allowed by PostgreSQL;
5. update `db/01_schema.sql`, `docs/tables.md`, and migration documentation.

The application must still preflight overlaps to return useful structured IDs;
the database constraint is the concurrency backstop, not a replacement for
business error translation.

## Payroll and contract deletion guards

“Payroll within the contract range” must use the worked calendar period, not only
`payment_date`, because a former employer may pay a final settlement after its
contract ended. For each `PAY_PERIOD`, derive the inclusive worked month from
`period_year`/`period_month` and treat a payroll as blocking when that month
intersects the contract interval.

This is consistent with the existing employer-change model:

- different employers may have payroll periods in the same year/month;
- a former employer may have a final settlement for days worked earlier in the
  month;
- `payment_date` remains relevant to payment and calculation rules, not as the
  sole contract-retention date.

The current `PAY_PERIOD` model does not store exact worked start/end dates, so
month overlap is the strongest supported deletion guard. Do not claim daily
allocation precision that the schema cannot provide.

Employer deletion is blocked when either of these exists:

- any `PAY_EMP_CONT` row with that `employer_id`;
- any `PAY_PERIOD` row with that `employer_id`.

Return structured blockers rather than exposing raw foreign-key errors. Do not
cascade-delete employers, contracts, or payrolls through this maintenance API.

## Atomicity and concurrency

Every collection mutation is all-or-nothing:

- prevalidate IDs, ownership, dates, overlap, deletion blockers, and duplicate
  request IDs before mutation;
- lock all target employers/contracts and all rows needed for deletion guards;
- apply creates/updates/deletes in one transaction;
- rollback the entire request on any application or database error;
- return no partial-success response.

For contract overlap, rely on the `pf-db` exclusion constraint as the race-safe
backstop and translate its narrow integrity error into the same `409` response.
For deletion-vs-payroll races, maintenance operations and payroll writes must
share an employer-scoped transaction lock or another explicitly coordinated
locking mechanism. A check followed by an unlocked delete is not sufficient.

## Error contract

Use existing `PayrollError` translation and focused structured details. Suggested
statuses:

| Situation | Status | Required detail |
| --- | --- | --- |
| malformed/non-positive/duplicate IDs | `400`/`422` per existing convention | field and index information |
| unknown employer/contract ID | `404` | missing IDs |
| unknown `employer_id` on contract mutation | `404` | employer ID |
| invalid date/indefinite combination | `422` | contract index and fields |
| overlapping contract interval | `409` | new interval plus conflicting contract IDs/ranges/employers |
| employer deletion blocked | `409` | employer ID, contract IDs, payroll IDs/counts |
| contract deletion blocked | `409` | contract ID and blocking payroll period IDs/ranges |
| concurrent unique/exclusion/deletion conflict | `409` | stable business detail, not raw DB text |

Error details must not expose payslip amounts, RUTs, health data, or other
unnecessary payroll information.

## Compatibility and rollout

No existing HTTP endpoint is removed. Payroll import, PDF preview, payroll
queries, and template management continue to consume stable employer IDs and
existing contract lookups.

The maintenance API is additive, but it introduces a schema migration and a
global contract-overlap rule. Before release:

- audit and remediate existing contract overlaps;
- apply the `pf-db` migration before traffic reaches code that depends on it;
- verify every consumer uses IDs for relationships;
- update `pf-payroll/docs/api.md`, OpenAPI, Postman, and migration docs;
- publish operational guidance for employer changes and final settlements.

No new infrastructure is required. The cost impact is one schema constraint,
additional CRUD queries, and small transaction/locking overhead. The cheaper
alternative — application-only overlap checks — is rejected because it is not
safe under concurrent writes.

## Testing recommendation

### Employer operations

- list, create, update, and delete employers;
- mixed create/update batch preserves existing IDs;
- unknown IDs never become creates;
- duplicate names follow the database uniqueness contract;
- deletion is blocked by contracts and by payrolls independently;
- employer rename preserves all relationships and IDs;
- failed batch rolls back every mutation.

### Contract operations

- required `started_at` and valid end-date semantics;
- real employer required; no orphan contract;
- create/update by ID presence;
- mixed create/update batch;
- overlap rejected across the same and different employers;
- adjacent intervals accepted;
- self-overlap on update ignored only for the row being updated;
- contract deletion blocked by worked-month payroll overlap;
- concurrent overlap writes cannot both succeed;
- failed batch rolls back completely.

Use synthetic data only. Stub external providers and test migration upgrade,
downgrade, and fresh-schema behavior through the existing `pf-db` test seam.

## Documentation and release gates

Update in the same implementation change:

- `modules/pf-payroll/docs/api.md`;
- generated OpenAPI and API tests;
- `postman/pf-ecosystem.postman_collection.json` with operational CRUD requests,
  not standalone negative-test requests;
- `pf-db/db/01_schema.sql`, migration docs, tables docs, and the migration;
- `docs/proposals/INDEX.md` status and final release evidence;
- the living implementation plan:
  `modules/pf-payroll/docs/proposals/employers-contracts-maintenance-plan.md`.

Before implementation, this recommendation requires explicit approval. Before
commit/push/deployment, follow the ecosystem workflow and owning repositories'
`AGENTS.md` authorization gates.

## Final recommendation

Proceed with separate ID-based employer and contract maintenance resources using
atomic batch create/update requests and explicit bulk deletion contracts. Keep
employer names unique but never use them as relationship keys. Require real
employers for contracts, globally reject overlapping contract intervals using a
coordinated PostgreSQL exclusion constraint, guard deletions against worked-month
payroll dependencies, and preserve the employer-change/final-settlement model.
