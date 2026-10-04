# Payroll Natural-Key Conflict Brief

**Date:** 2026-10-04
**Status:** planned
**Level:** L
**Scope:** `pf-payroll` structured payroll import API and its documented ecosystem contract
**Related artifacts:** [`payroll-update-delete-brief.md`](payroll-update-delete-brief.md), [`payroll-update-delete-recommendation.md`](payroll-update-delete-recommendation.md)
**Approved:** none
**Superseded-by:** none

## Objective

Change the structured payroll import contract so that a period submitted without
`period_id` is treated as an insertion only. If the submitted natural identity
already exists — the same employer, period year, and period month — the request
must fail instead of silently overwriting the existing payroll period.

The error must identify the existing conflicting `period_id` so the caller can
choose whether to resubmit the complete payload as an explicit update or correct
the natural-key values.

## Current behavior

`POST /payroll/import/json` currently infers the operation from `period_id`:

- `period_id` present: update the identified period after strict identity checks;
- `period_id` omitted or `null`: use the legacy natural-key import path;
- an omitted ID whose `(employer, period_year, period_month)` already exists is
  currently loaded and overwritten in place, preserving its database ID.

This brief changes only the last behavior. The existing explicit-ID update
contract remains available and must not silently change.

## Desired contract

Each period block is classified as follows:

| Submitted `period_id` | Natural key exists? | Required behavior |
| --- | --- | --- |
| omitted or `null` | no | Insert a new period using the existing import/reconciliation pipeline. |
| omitted or `null` | yes | Reject the complete request with `409 Conflict`; do not modify or insert anything. Include the conflicting `period_id`. |
| positive existing ID | matching natural key | Update that exact period in place, preserving its primary key. |
| positive existing ID | mismatched natural key | Reject with the existing identity-conflict behavior; do not move or create a period. |
| positive missing ID | any | Return `404 Not Found`; never fall back to natural-key insertion. |

`mode` retains its existing meaning:

- `mode="commit"` commits only after the complete batch succeeds;
- `mode="validate"` executes the real pipeline and rolls back all writes.

A natural-key conflict is an input/business conflict, not a reconciliation
conflict. It must be reported before any period mutation occurs and must resolve
the transaction as `validate` in either mode.

## Batch semantics and payroll-period invariants

A single `POST /payroll/import/json` request may contain multiple period blocks.
The request may mix operations simultaneously:

- blocks with no `period_id` are candidate insertions;
- blocks with an existing `period_id` are explicit updates;
- each block is classified independently before any mutation.

The complete request remains atomic. In `mode="commit"`, if any block fails
because of a natural-key conflict, invalid data, reconciliation, capacity, or a
missing eligible employment-contract interval, the system must persist **none**
updates in that request. In `mode="validate"`, the same complete batch is
calculated against the transaction and then rolled back.

The following database/business invariants must hold after every successful
commit:

1. There is at most one `PAY_PERIOD` row for each
   `(employer_id, period_year, period_month)`.
2. Different employers may each have one payroll period for the same
   `(period_year, period_month)`.
3. The sum of `worked_days` across all payroll periods sharing a
   `(period_year, period_month)` — regardless of employer — must be at most 30.
   An update must calculate the projected total after replacing the target's old
   `worked_days`; an insert must add its submitted value. A mixed batch must be
   validated as one projected final state.
4. A payroll period for employer A may coexist with a payroll period for
   employer B in the same calendar month when the employment intervals explain a
   change of employer during that month. For example, employer A may pay a final
   settlement for the first days and employer B may pay the remaining days.
5. A new period may be created only when the submitted employer has an active
   employment contract overlapping the worked calendar month represented by
   `period_year`/`period_month`. It must **not** require the contract to be active
   on `payment_date`: a former employer may pay a final settlement at month-end
   or later for days worked earlier in that month.

Each invariant failure must identify the relevant employer/period or year/month
and must leave the complete request unchanged.

## Error response

Use the existing application error translation layer and return HTTP `409` for a
natural-key conflict. Capacity and contract-eligibility errors must use stable
structured business-error responses as resolved by the recommendation.

conflict in the request. For a single conflict, the minimum shape is:

```json
{
  "message": "A payroll period already exists for the submitted employer and period.",
  "conflicting_periods": [
    {
      "period_id": 481,
      "employer": "Synthetic Employer",
      "period_year": 2026,
      "period_month": 8
    }
  ]
}
```

The exact surrounding fields may follow the repository's established structured
error convention, but the existing database `period_id` is mandatory. Do not
return the submitted `period_id` because it was omitted/null; the useful identity
is the persisted conflicting row's ID.

For a batch containing multiple conflicts, return all detected conflicts in one
error where this can be done without weakening atomicity. Do not report a partial
success. If conflict discovery requires loading several rows, perform it before
mutation and preserve the existing transaction rollback behavior.

## Scope

### In scope

- `POST /payroll/import/json` period classification and natural-key conflict
  detection;
- application error/DTO shape needed to carry the existing conflicting ID;
- repository lookup semantics, locking, and projected period-capacity checks;
- active employment-contract validation for insertions;
- atomic mixed-batch behavior for simultaneous insertions and explicit updates;
- validation of the global `worked_days` total per year/month;
- validation that insertions have an active employer contract;
- unit, integration, and rollback tests;
- `modules/pf-payroll/docs/api.md`;
- generated OpenAPI verification;
- root Postman collection examples and expected conflict response;
- the related implementation plan and lifecycle index status.

### Out of scope

- changing the explicit `period_id` update semantics;
- adding a separate update route or `PATCH` operation;
- changing the natural-key definition;
- changing payroll calculation, contribution, tax, or reconciliation algorithms;
- changing spreadsheet import behavior unless the implementation audit proves it
  shares the same public contract and the recommendation explicitly approves it;
- changing `pf-db` schema or adding a uniqueness constraint without a separate
  recommendation and migration decision;
- deletion, soft-delete, audit retention, or historical payroll policy.

## Compatibility and safety requirements

- Existing callers that submit genuinely new natural keys without `period_id`
  continue to insert successfully.
- Existing callers that intentionally update a period must include its
  `period_id` and complete period payload.
- A caller cannot accidentally overwrite another period by omitting an ID.
- A missing target ID never becomes an insert.
- A natural-key conflict never partially mutates a request, including mixed
  insert/update batches.
- The persisted conflicting `period_id` is stable and usable in a follow-up
  explicit update request.
- The behavior is deterministic under concurrent requests; the recommendation
  must decide whether an application-level lock, a database constraint, or an
  equivalent transaction-safe check is required.
- No real payslip, RUT, salary, health information, or credential may enter
  fixtures or documentation.

## Acceptance criteria

The recommendation must turn these outcomes into executable verification:

1. A new natural-key period without `period_id` still inserts.
2. An existing natural-key period without `period_id` returns `409` and includes
   the persisted `period_id` in structured error detail.
3. The natural-key conflict leaves the existing header, items, plan snapshots,
   summary, and neighboring periods unchanged.
4. An explicit update with the returned `period_id` still updates in place and
   preserves the ID.
5. A mixed batch containing a natural-key conflict rolls back every insert and
   update in the request.
6. `mode="validate"` discovers the conflict and leaves every `pf-payroll` row
   unchanged.
7. A missing explicit `period_id` continues to return `404`, never insert, and
   never use natural-key fallback.
8. Duplicate natural keys within one request retain their existing validation
   behavior and are distinguished from a conflict with an already persisted row.
9. OpenAPI, `docs/api.md`, and Postman describe the same `409` response and
   conflicting-ID field.
11. A mixed batch may contain both insertions and explicit updates, and a fully
    successful commit persists all of them atomically.
12. If any block in a commit batch fails, no block is persisted; test failures
    for natural-key, reconciliation, capacity, contract, and database errors.
13. Multiple employers may share the same year/month, subject to the global
    `worked_days` capacity rule.
14. An insertion without an active employer contract is rejected before mutation.
15. An insertion or update that would make the year/month `worked_days` total
    exceed 30 is rejected with a structured capacity error.

## Required design questions

The recommendation must resolve these questions before implementation:

1. Should the repository preflight all no-ID natural keys before processing any
   period, or may it detect them sequentially inside the transaction while still
   guaranteeing a complete conflict list and no mutation?
2. What application error/DTO best fits the existing error translation layer
   without duplicating the natural-key lookup or the explicit-ID identity check?
3. Is a row lock sufficient for concurrent no-ID inserts, or is a coordinated
   `pf-db` uniqueness constraint/migration required to prevent a race between
   conflict check and insert?
4. Does the conflict response use the existing `conflicting_periods` shape or a
   dedicated `natural_key_conflicts` field? Choose one stable contract and update
   all documentation consistently.
5. Does the rule apply only to JSON structured import, or should spreadsheet
   import receive the same insert-only natural-key behavior in a separate,
   explicitly scoped change?
7. How should the error statuses and structured fields distinguish natural-key
   conflicts, worked-days capacity conflicts, and missing eligible contract
intervals?
8. How should a mixed batch calculate its projected year/month worked-days total
   when it contains multiple inserts and updates in the same request?
9. Does the contract-overlap rule apply only to insertions, or must explicit
   updates also require a contract interval overlapping the worked month?
10. What exact contract interval boundary applies on `started_at` and `ended_at`
    dates, and how should overlapping contracts be handled?
11. What database locking/query strategy prevents concurrent batches from both
    passing the worked-days capacity check before commit?
12. What rollout/compatibility communication is needed for clients that currently
    rely on natural-key overwrite behavior?

## Required artifacts and release gates

Before implementation:

- produce and approve a recommendation answering every design question;
- create a living implementation plan because this is a Level L public-contract
  change;
- register/update the slug in `docs/proposals/INDEX.md`;
- identify whether `pf-db` must own a uniqueness migration before service code;
- identify real ecosystem consumers and update only those that require changes.

During implementation:

- record deviations and discoveries in the living plan;
- keep calculation logic shared with the existing import pipeline;
- update `docs/api.md`, OpenAPI checks, and Postman in the same change;
- test with synthetic data and stub external market-data calls.

Before release:

- run the complete `pf-payroll` quality gate;
- validate migration upgrade/downgrade if schema changes are approved;
- verify the conflict response against OpenAPI, API docs, and Postman;
- obtain explicit authorization for commit, push, and any deployment approval;
- monitor CI and record the final outcome in the plan and index.

## Expected recommendation deliverable

Write the corresponding recommendation to:

```text
modules/pf-payroll/docs/proposals/payroll-natural-key-conflict-recommendation.md
```

The recommendation must remain a design decision record. Implementation progress,
corrections, test results, deployment IDs, and release evidence belong in:

```text
modules/pf-payroll/docs/proposals/payroll-natural-key-conflict-plan.md
```
