# Employers and Employment Contracts Maintenance Brief

**Date:** 2026-10-04
**Status:** planned
**Level:** L
**Scope:** `pf-payroll` employer and employment-contract maintenance API, persistence rules, and ecosystem documentation
**Related artifacts:** [`payroll-natural-key-conflict-recommendation.md`](payroll-natural-key-conflict-recommendation.md)
**Approved:** none
**Superseded-by:** none

## Objective

Expose supported HTTP operations to maintain the real employers and their
employment contracts used by payroll import, payroll calculations, and PDF
preview:

- create, list, modify, and delete employers;
- create, list, modify, and delete employment contracts;
- accept multiple mutations in one request;
- allow a request to mix creates and explicit updates;
- enforce temporal, referential, payroll-retention, and transaction invariants.

This is a backend/API feature. It must preserve the existing hexagonal layering,
financial precision, and contract lookup behavior used by payroll processing.

## Current context

The current database model already contains:

- `PAY_EMPLOYER`, with a unique employer `id` y `name`, tax/country fields, payment-date
  configuration, and salary-increase configuration;
- `PAY_EMP_CONT`, with `employer_id`, required `started_at`, nullable `ended_at`,
  `is_indefinite`, and optional `position`;
- `PAY_PERIOD`, which references `PAY_EMPLOYER` and has a unique
  `(employer_id, period_year, period_month)` key;
- existing read-side contract lookup by employer and payment date, used by
  payroll queries and preview-related flows.

No dedicated HTTP CRUD surface currently owns employer or contract maintenance.
The new API must not bypass the existing repository/application boundaries or
create a second incompatible contract-selection algorithm.

## Employer identity and relationships

`PAY_EMPLOYER.name` remains unique because it is a human-facing employer
attribute, but it is **not** the relational identity of an employer.

All relationships within the database and across application/service boundaries
must use the stable numeric `employer_id`:

- `PAY_PERIOD.employer_id` references `PAY_EMPLOYER.id`;
- `PAY_EMP_CONT.employer_id` references `PAY_EMPLOYER.id`;
- PDF-template and other employer-owned records reference the employer ID;
- contract and employer DTOs, ports, repository methods, and maintenance API
  payloads use `employer_id`/`id`, not employer name, as the relationship key;
- consumers must not persist or join on `name` as a substitute for the ID.

Employer `name` may be returned for display and may be used as an explicit
human-facing search/filter value where a separate contract allows it. A name
change must update only the employer attribute; it must never create a new
employer, change `employer_id`, detach contracts, or move payrolls/templates to
another employer.

The existing payroll JSON import accepts an employer name for historical input
compatibility. That legacy boundary must not weaken the new maintenance API's
ID-based relationship contract; if name-to-ID resolution remains necessary for
legacy imports, it must be an explicit adapter concern and must resolve to one
persisted employer ID before persistence.

## Core API intent

The exact route names and request/response DTOs belong to the recommendation, but
the public contract must support these semantics:

### Create/update by ID presence

For employer and contract mutation requests:

- omitted or `null` `id` means create;
- a positive existing `id` means update that exact record in place;
- a positive missing `id` is an error and must never become a create;
- IDs must be positive and unique within a request;
- a request may contain multiple creates, multiple updates, or a mixture of both;
- the complete request is atomic: no partial success response and no partial
  persistence.

The recommendation must decide whether employers and contracts use one combined
batch resource or separate collection endpoints while preserving the same
identity semantics.

### Listing

The API must expose list/read operations sufficient to:

- list employers with stable IDs and maintenance-relevant fields;
- list contracts, optionally filtered by employer and/or effective date range;
- return contract intervals and their employer identity without requiring clients
  to infer relationships from payroll records.

Pagination, filtering, sorting, and maximum page size must be designed against
actual expected volumes rather than guessed infrastructure needs.

### Deletion

The API must define single and/or bulk deletion semantics explicitly. Deletion
must be transactional and must return structured conflicts identifying the
employer or contract that cannot be deleted and the payroll/contract references
that block it.

## Required business invariants

The implementation and recommendation must enforce at least the following:

1. Every contract has a non-null `started_at`.
2. `ended_at`, when present, is not earlier than `started_at`.
3. `is_indefinite` is consistent with `ended_at` according to one documented
   rule; the API must not allow contradictory combinations silently.
4. No contract validity interval may overlap any other contract validity interval,
   regardless of employer. This is a global constraint across `PAY_EMP_CONT`, not
   merely a per-employer constraint.
5. Every contract references an existing employer; orphan contracts are never
   accepted.
6. Employer identity is stable and all relationships use `employer_id`/`id`;
   the unique `name` is not a foreign-key or service-integration key.
7. An employer deletion is rejected when that employer has any contract or any
   payroll period associated with it.
8. A contract deletion is rejected when any payroll period falls within the
   contract's applicable date range. The recommendation must define whether
   “falls within” uses the payroll worked calendar month, `payment_date`, or an
   explicit overlap rule; it must align with the employer-change/final-settlement
   model already being introduced for payroll periods.
9. Employer and contract mutations are atomic per request. If one item fails,
   every create/update/delete in the request rolls back.
10. Concurrent writes cannot bypass the global non-overlap rule or delete a
   contract/employer after a concurrent payroll write makes deletion unsafe.
11. Existing payroll calculations and imports continue to resolve a valid
    contract deterministically after maintenance changes.

## Employer-change scenario

The design must support a worker changing employers inside one calendar month.
Two employers may therefore have payroll periods for the same year/month, subject
to the payroll period rules already defined elsewhere. A former employer may
have a final settlement paid after its contract ended.

The contract-maintenance API must not reject that legitimate final settlement by
requiring the old contract to be active on `payment_date`. Contract interval
validation and payroll deletion checks must distinguish the worked calendar
period from the payment date where the domain requires it.

## Atomic batch examples

### Mixed employer create/update

The recommendation contains the canonical synthetic batch example; this brief intentionally avoids duplicating request JSON.

## Error contract requirements

Use the existing application error translation layer and define stable structured
errors for at least:

- malformed or duplicate IDs;
- unknown employer/contract IDs;
- unknown `employer_id` on contract creation/update;
- overlapping contract intervals, including the conflicting contract IDs and
  intervals;
- invalid date interval or inconsistent indefinite-contract fields;
- contract deletion blocked by payroll periods;
- employer deletion blocked by contracts or payroll periods;
- concurrent integrity/deletion conflicts.

The recommendation must select stable HTTP statuses, preserve all-or-nothing
semantics, and ensure error details never expose sensitive payroll data.

## Compatibility and safety requirements

- Existing payroll import and calculation callers continue to work with employer
  and contract IDs created or modified by the new API.
- Employer names remain unique according to the database contract; the
  recommendation must decide whether comparison is exact, case-sensitive, or
  normalized before persistence.
- No contract can be created for an unknown employer, including in a mixed batch
  where that employer is created in the same request, unless the recommendation
  explicitly defines and tests same-request dependency ordering.
- Updates must not silently change immutable identity fields or move a contract
  across another contract's interval.
- Deletion is hard deletion only if the existing legal/audit policy permits it;
  otherwise stop and redesign around archive/soft-delete semantics.
- Synthetic data only: no real payslips, RUTs, salaries, health data, or secrets.

## Acceptance criteria

The recommendation must convert these outcomes into executable tests and API
checks:

1. Create one employer and list it with its stable ID.
2. Update an existing employer by ID and preserve its ID.
3. Reject an unknown employer ID; never silently create it.
4. Create multiple employers in one atomic request.
5. Mix employer creates and updates in one successful atomic request.
6. Reject the complete employer batch when one item is invalid.
7. Create a contract with a required `started_at` and real employer ID.
8. Update a contract by ID and preserve its ID.
9. Reject a contract with an unknown employer ID.
10. Reject any interval overlap globally, including overlaps across employers.
11. Mix contract creates and updates in one successful atomic request.
12. Reject a mixed contract batch atomically when one interval overlaps another
    or violates an employer/deletion invariant.
13. List contracts by employer and effective date range.
14. Reject contract deletion when payroll periods fall inside the defined
    applicable date range.
15. Reject employer deletion when contracts exist.
16. Reject employer deletion when payroll periods exist, even if contracts do
    not.
17. Verify concurrent interval writes and deletion-vs-payroll races cannot
    violate the invariants.
18. Verify OpenAPI, service API docs, Postman, tests, and the final implementation
    describe the same operations and statuses.

## Required design questions

The recommendation must resolve these questions before implementation:

1. What exact route/resource shape should employers and contracts use, and should
   their batch bodies be separate or combined?
2. Which employer fields are mutable after payroll data exists? Is `name` mutable,
   and if so, how are external consumers and historical references protected?
3. Is a global no-overlap rule truly intended across different employers, or should
   it apply only to contracts for the same worker/person? The current schema has
   no employee identity column, so the recommendation must explicitly preserve or
   challenge this requirement rather than silently reinterpret it.
4. What is the exact interval convention: inclusive dates, half-open ranges, and
   treatment of `ended_at = started_at`?
5. How should `is_indefinite` and nullable `ended_at` map to one another?
6. Does same-request employer creation followed by contract creation work in one
   request? If yes, define dependency ordering and temporary/reference identity;
   if no, return a clear error and require separate calls.
7. For contract deletion, does “payroll within the range” use worked calendar
   month overlap, payment date, or another domain date?
8. Should employer deletion expose separate blockers for contracts and payrolls,
   and what structured IDs/counts are safe to return?
9. Is hard deletion allowed by product/legal/audit requirements, or is archive
   required?
10. Does the existing database schema need exclusion constraints, indexes, or
    cascade/restrict changes? Coordinate any migration in `pf-db` before consumer
    code.
11. What pagination and maximum batch sizes are appropriate for expected volumes?
12. Which ecosystem consumers, Postman requests, API docs, and operational
    workflows need updates?

## Scope boundaries

### In scope

- employer and employment-contract HTTP resource design;
- atomic batch create/update semantics based on ID presence;
- list/filter/read contracts;
- deletion guards and structured conflicts;
- repository ports, DTOs, use cases, adapters, routes, tests, API docs,
  OpenAPI, Postman, and coordinated schema changes if required.

### Out of scope unless explicitly added by recommendation

- payroll calculation formula changes;
- changing payroll period import natural-key semantics;
- changing the spreadsheet import contract;
- employee/person identity modeling;
- historical payroll reclassification;
- audit/archive policy redesign;
- bulk mutation through CLI commands.

## Required artifacts and release gates

Before implementation:

- produce and approve a recommendation answering every design question;
- create a living implementation plan because this is a Level L public API and
  integrity change;
- audit all foreign keys, indexes, constraints, and current consumers;
- coordinate any schema migration through `pf-db`;
- update the central improvement index status.

During implementation:

- keep orchestration in application use cases and repository access behind ports;
- record deviations and findings in the living plan;
- update service `docs/api.md`, OpenAPI checks, and Postman in the same change;
- use synthetic fixtures and stub external dependencies.

Before release:

- run the owning repository's complete quality gate and migration tests;
- verify all-or-nothing behavior and concurrency safeguards;
- obtain explicit commit, push, and deployment authorization;
- clean superseded active workflows before pushing;
- monitor CI and smoke tests, then record final evidence in the plan and index.

## Expected recommendation deliverable

Write the corresponding recommendation to:

```text
modules/pf-payroll/docs/proposals/employers-contracts-maintenance-recommendation.md
```

Implementation progress and release evidence belong in:

```text
modules/pf-payroll/docs/proposals/employers-contracts-maintenance-plan.md
```
