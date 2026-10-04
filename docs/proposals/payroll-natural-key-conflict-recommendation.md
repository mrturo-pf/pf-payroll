# Payroll Natural-Key Conflict Recommendation

**Date:** 2026-10-04
**Status:** approved
**Level:** L
**Scope:** `pf-payroll` structured JSON import and its ecosystem documentation contract
**Related artifacts:** [`payroll-natural-key-conflict-brief.md`](payroll-natural-key-conflict-brief.md), [`payroll-natural-key-conflict-plan.md`](payroll-natural-key-conflict-plan.md), [`payroll-update-delete-recommendation.md`](payroll-update-delete-recommendation.md)
**Approved:** 2026-10-04 by Arturo (conversation)
**Superseded-by:** none

## Executive recommendation

Change only the structured JSON import path so that an omitted/null
`period_id` is insertion-only. Before any mutation, preflight every no-ID period
against the existing natural key `(employer_id, period_year, period_month)`.
If any target already exists, reject the entire request with `409 Conflict` and
return the persisted conflicting `period_id` values in a dedicated structured
field named `natural_key_conflicts`.

Keep explicit-ID updates exactly as they work today. A caller that intends to
modify an existing period must include its `period_id`; the update remains
identity-aware, locks the target, verifies its employer/year/month identity, and
preserves the primary key.

Do not add a database migration. `pf-db` already defines
`UNIQUE (employer_id, period_year, period_month)` on `PAY_PERIOD` in migration
`0002_payroll_schema.py`, and the current schema documentation records the same
constraint. That constraint is also the concurrency backstop for two no-ID
requests racing to insert the same natural key.

## Why this is the smallest safe change

The current repository intentionally treats a natural-key hit as an import/upsert:
load the existing period, replace its header/items, and run reconciliation. That
behavior predates explicit identity-aware updates and is now unsafe because an
omitted ID can silently overwrite an existing payroll.

The requested rule can be implemented at the structured-period boundary without
copying calculation logic or changing the explicit update path:

```text
JSON block with ID      -> existing strict update path
JSON block without ID   -> preflight natural key
                         -> absent: existing insert path
                         -> present: 409, no mutation
```

Spreadsheet import remains unchanged in this feature. Its natural-key behavior
is a separate compatibility decision requiring its own brief if product wants
insert-only semantics there too.

## Public contract

### Operation classification

| Input | Existing natural key | Behavior |
| --- | --- | --- |
| `period_id` omitted/null | no | Insert through the existing import and reconciliation pipeline. |
| `period_id` omitted/null | yes | `409 Conflict`; no insert/update/delete. |
| positive existing `period_id` | matching identity | Update in place; preserve ID; replace complete item set. |
| positive existing `period_id` | mismatched identity | Existing `409` identity conflict; no mutation. |
| positive missing `period_id` | any | Existing `404`; never natural-key fallback. |

`mode` remains transaction behavior only:

- `commit`: commit only after the complete batch succeeds and reconciles;
- `validate`: run the real pipeline and roll back all `pf-payroll` writes.

A natural-key conflict is discovered before the import/reconciliation pipeline,
so it returns `409` in both modes. The route must resolve the transaction as
`validate` before translating the error.

### Batch semantics and invariants

The endpoint must allow a single request to mix candidate insertions and explicit
updates. Each block is classified independently by `period_id`, but the request
is one transaction:

```text
insert A + update B + insert C → all persist only if every block succeeds
insert A + update B + conflict C → nothing persists
```

This applies to `mode="commit"`: any natural-key conflict, identity conflict,
reconciliation failure, capacity failure, missing eligible contract interval, or
unrelated
persistence error must roll back every operation in the request. `mode="validate"`
runs the same projected batch and rolls it back regardless of outcome.

The successful projected state must satisfy these invariants:

1. `PAY_PERIOD` has at most one row per `(employer_id, period_year,
   period_month)`. This is already enforced by the existing `PAY_PERIOD` unique
   constraint in `pf-db`.
2. Different employers may each have one row for the same `(period_year,
   period_month)`.
3. The sum of `worked_days` across all employers for one `(period_year,
   period_month)` is at most 30. For an explicit update, subtract the target's
   current value before adding the submitted value. For a mixed batch, calculate
   the final projected total across all inserts and updates, not one block at a
   time.
4. A new period requires an employment contract interval overlapping the worked
   calendar month represented by `period_year`/`period_month`. It must not require
   the contract to remain active on `payment_date`: a former employer may pay a
   final settlement at month-end or later for days worked earlier in the month.

A change of employer during a month is therefore valid when the periods belong to
different employers and their contract intervals explain the transition. For
example, employer A can have a final settlement for the first days and employer
B can have the regular settlement for the remaining days. The global
`worked_days <= 30` rule remains the capacity check for the projected final state.

These are separate business errors from natural-key identity conflicts and must
have stable structured fields so clients can distinguish them.

### Contract eligibility and payment timing

The current query reader selects contracts by `employer_id` and
`PAY_PERIOD.payment_date`, which is useful for calculations tied to the payment
date but is not sufficient for validating a final settlement after a change of
employer. The natural-key creation rule must use the worked calendar month as
its eligibility window:

```text
contract.started_at <= month_end
and (contract.ended_at is null or contract.ended_at >= month_start)
```

The exact inclusive-boundary and overlapping-contract policy must be tested and
aligned with the employment-contract domain rules. Do not silently reuse the
payment-date lookup for this creation guard. `payment_date` remains available
for market-data and payroll-calculation rules that explicitly require it.

Because `PAY_PERIOD` currently stores only `period_year`, `period_month`, and a
`worked_days` total, this rule can prove contract overlap with the calendar month
and enforce the aggregate day capacity, but it cannot prove the exact day
allocation (for example, employer A worked days 1–5 and employer B days 6–30).
It also cannot detect a same-day overlap or a gap inside the month. Strict
day-level validation would require storing worked start/end dates or a separate
daily allocation model and is outside this change. Do not imply that the
month-overlap plus total-days check provides that stronger guarantee.


The existing unique constraint solves duplicate natural keys, but it cannot
protect the aggregate `worked_days <= 30` invariant. The implementation should
serialize capacity validation per `(period_year, period_month)` using a
transaction-scoped PostgreSQL advisory lock, or an equivalent existing locking
primitive if the codebase already has one. Then it must lock/read all affected
`PAY_PERIOD` rows, calculate the projected final total, and reject before any
mutation when the total would exceed 30.

No new table or migration is recommended. If the team rejects advisory locks,
that is a design change requiring an explicit concurrency alternative; a plain
unlocked read is not sufficient because two concurrent batches could both pass
the check.


Use `PayrollConflictError` with structured `detail`; do not add a new HTTP
exception hierarchy. Add a focused DTO/helper for the conflict payload so the
application layer does not depend on FastAPI models.

Recommended response:

```json
{
  "detail": {
    "message": "A payroll period already exists for the submitted natural key.",
    "natural_key_conflicts": [
      {
        "period_id": 481,
        "employer": "Synthetic Employer",
        "period_year": 2026,
        "period_month": 8
      }
    ]
  }
}
```

`natural_key_conflicts` is deliberately distinct from the existing
`conflicting_periods` reconciliation field. It prevents clients from confusing
an identity collision with a declared-versus-computed payroll conflict.

Use separate stable fields for the new invariants:

```json
{
  "message": "The submitted payroll would exceed the worked-days capacity.",
  "worked_days_conflicts": [
    {
      "period_year": 2026,
      "period_month": 8,
      "current_worked_days": "24",
      "requested_worked_days": "10",
      "projected_worked_days": "34",
      "maximum_worked_days": "30"
    }
  ]
}
```

A missing eligible contract should be a stable business error containing the
employer identity, worked calendar month, and a reason that no contract interval
overlaps that month. It must not describe the failure as “no contract effective
on payment_date”, because a former employer may pay a final settlement after its
contract ended. The recommendation must choose the final HTTP status consistently
with existing application conventions; `409 Conflict` is appropriate when the
persisted contract state conflicts with the requested creation, while malformed
contract input remains validation error behavior.

For multiple conflicts, return every conflict found during preflight, sorted by
request period order or another documented deterministic order. The persisted
`period_id` is mandatory; `employer`, `period_year`, and `period_month` make the
error actionable without another lookup.

## Architecture and implementation shape

### Interface/application boundary

- Keep `ImportPayrollPeriodRequest.period_id: int | None` unchanged.
- Keep `_validate_period_ids()` for positive/duplicate explicit IDs.
- Map JSON blocks to an application-level period command or equivalent grouped
  representation so natural-key preflight is period-level, not duplicated per
  item row.
- Do not put repository access in Pydantic validators.
- Keep the route thin: map the request, invoke the use case, resolve the scope,
  and translate `PayrollError`.

### Application/use-case boundary

The structured JSON import use case should explicitly distinguish:

1. no-ID natural-key preflight;
2. eligible contract-interval validation for insertions;
3. projected worked-days capacity validation for the full batch;
4. explicit-ID update/import rows;
5. the shared `ProcessImportedPayrollPeriods` calculation pipeline.

Do not make generic row persistence guess whether a missing ID means insert or
update. The operation intent and projected period invariants should be validated
before persistence.

### Repository boundary

Add a narrow repository operation for preflight, for example:

```python
async def find_periods_by_natural_keys(
    self, keys: list[PayrollNaturalKeyDTO]
) -> list[PayrollNaturalKeyConflictDTO]: ...
```

The implementation should:

1. resolve employer names to existing `PAY_EMPLOYER` IDs using the same exact-name
   semantics as the current importer;
2. query all no-ID `(employer_id, year, month)` keys in one operation where
   practical;
3. validate employment-contract intervals overlapping the worked calendar month
   (not contracts effective on `payment_date`) for every insertion;
4. acquire the year/month capacity lock and calculate projected `worked_days`
   totals across the complete batch;
5. return persisted IDs for natural-key matches;
6. raise structured business errors before calling the mutating import branch;
7. leave explicit-ID updates on their current row-lock/identity-check path.

The existing `PAY_PERIOD` unique constraint is the race-condition backstop. If a
concurrent request wins the insert after preflight, catch the database integrity
error at the repository/application boundary, roll back the failed transaction,
look up the persisted conflicting row, and translate it to the same stable `409`
`natural_key_conflicts` shape. Do not expose a raw database error and do not turn
the race into an overwrite.

No `pf-db` migration is required unless the live schema audit discovers that the
existing unique constraint is absent from a supported environment. If that occurs,
stop and create a coordinated idempotent migration with downgrade before changing
consumer behavior.

## Transaction and concurrency behavior

- Preflight all no-ID keys before any period mutation in the request.
- Validate the eligible contract interval and projected worked-days capacity for
  the complete batch.
- If one conflict exists, mutate nothing, including valid explicit updates and
  valid inserts in the same batch.
- `mode="validate"` still reports a natural-key conflict; it is not a preview of
  a potentially destructive overwrite.
- Explicit-ID updates remain atomic with the batch and retain their existing
  row-lock behavior.
- The database unique constraint handles the check-then-insert race.
- Integrity-error translation must be narrow enough not to mask unrelated
  database failures; unrelated constraint errors retain their existing handling.
- Neighboring employers/periods remain untouched.

## Compatibility and rollout

This is a deliberate behavior tightening for clients that currently rely on
natural-key overwrite. Existing callers creating genuinely new periods are
backward-compatible. Existing callers modifying periods must change their
payload to include the persisted `period_id`.

Before release:

- search versioned ecosystem consumers of `/payroll/import/json`;
- update Postman examples to show insert with a new natural key, explicit update,
  and a `409` natural-key conflict;
- document the migration path in `docs/api.md`;
- communicate that resending a payload without `period_id` is no longer an
  overwrite operation.

No infrastructure is added and no cloud cost increase is expected. The existing
unique index and one preflight lookup are cheaper than adding a new service or
always-on coordination layer. The operational cost is a possible extra database
read per JSON import and client remediation for callers relying on overwrite;
measure preflight query latency and conflict frequency after release.

## Testing recommendation

### Application/interface

- no-ID new natural key remains a successful insert;
- an existing natural-key conflict returns `409` with the persisted ID;
- mixed insert/update requests are accepted when every block is valid;
- mixed insert/update requests roll back completely when any block fails;
- worked-days capacity conflicts return the projected/current totals;
- insertion without an eligible contract interval returns the chosen structured
  business error;
- multiple existing no-ID keys return all conflicts deterministically;
- explicit update remains successful and preserves its ID;
- missing explicit ID remains `404` with no natural-key insert;
- duplicate natural keys inside one request remain the existing validation error;
- error translation preserves the structured `natural_key_conflicts` detail.

### Transaction/integration

- a mixed batch containing insertions and explicit updates commits all operations
  when every block succeeds;
- a conflict in a mixed insert/update batch rolls back every write;
- validation mode performs no durable write and still reports the conflict;
- existing items, headers, snapshots, summary, and neighboring periods remain
  unchanged after conflict;
- a concurrent no-ID insert cannot create two rows or overwrite the winner;
- concurrent batches cannot both pass the same year/month `worked_days <= 30`
  check;
- the existing `PAY_PERIOD` unique constraint is asserted in the migration/schema
  test seam without introducing a new migration.

Use synthetic data only and stub pf-rates through the existing test seam.

## Documentation and release gates

Update in the same implementation change:

- `modules/pf-payroll/docs/api.md` with no-ID insert-only semantics and `409`
  `natural_key_conflicts` examples;
- generated OpenAPI and route tests;
- `postman/pf-ecosystem.postman_collection.json` with insert, explicit update,
  and conflict examples;
- the living implementation plan at
  `modules/pf-payroll/docs/proposals/payroll-natural-key-conflict-plan.md`;
- `docs/proposals/INDEX.md` status and release evidence.

Before implementation, this recommendation requires explicit approval. Before
commit/push/deployment, follow the ecosystem workflow's separate authorization
gates.

## Final decision

Approve the following design unless a user decision changes it:

1. no `period_id` means insert-only for `POST /payroll/import/json`;
2. an existing natural key returns `409` with persisted `period_id` details;
3. explicit-ID update behavior remains unchanged;
4. preflight occurs before mutation and is atomic for mixed batches;
5. the existing `PAY_PERIOD` unique constraint is the concurrency backstop;
6. no `pf-db` migration is needed;
7. spreadsheet import is out of scope;
8. implementation progress belongs in the separate living plan.
