# Payroll Natural-Key Conflict Implementation Plan

**Date:** 2026-10-04
**Status:** in progress
**Level:** L
**Scope:** `pf-payroll` structured JSON import and ecosystem contract
**Related artifacts:** [`payroll-natural-key-conflict-brief.md`](payroll-natural-key-conflict-brief.md), [`payroll-natural-key-conflict-recommendation.md`](payroll-natural-key-conflict-recommendation.md)
**Approved:** 2026-10-04 by Arturo (conversation)
**Superseded-by:** none

## Objective

Make `POST /payroll/import/json` insertion-only when `period_id` is omitted or
null and the natural key is new; return structured `409 Conflict` instead of
silently overwriting an existing natural key. Preserve explicit-ID updates,
atomic mixed batches, month-level worked-day capacity, and worked-month contract
eligibility.

## Implementation slices

| Slice | Description | Status | Exit criteria |
| --- | --- | --- | --- |
| 1 | Application error/DTO and structured JSON preflight | validated | All no-ID natural keys are checked before mutation; conflicts include persisted IDs. |
| 2 | Worked-days projected capacity and contract interval validation | validated | Inserts/updates are validated against the complete projected batch and worked calendar month. |
| 3 | Transaction/concurrency behavior | validated | Existing unique constraint and capacity locking prevent races; unrelated integrity errors remain distinct. |
| 4 | Tests | validated | Full suite, preflight unit coverage, and endpoint tests cover mixed batches, rollback, conflicts, capacity, contracts, and explicit updates. |
| 5 | Docs/OpenAPI/Postman | validated | API docs, generated OpenAPI, Postman, brief/recommendation/plan agree. |
| 6 | Quality/release | in progress | Local quality gate passes; commit, push, CI, and deployment evidence remain. |

## Decisions being implemented

- Scope is `POST /payroll/import/json`; spreadsheet import is unchanged.
- `period_id` absent/null means insert-only, never natural-key overwrite.
- Existing natural-key conflicts return `409` with `natural_key_conflicts`.
- Mixed insert/update batches are allowed and all-or-nothing.
- `PAY_PERIOD` already has the natural-key unique constraint; no migration is expected.
- Contract eligibility uses overlap with the worked calendar month, not activity at `payment_date`.
- Global `worked_days` capacity is at most 30 per calendar year/month across employers.
- Exact day allocation is not validated because `PAY_PERIOD` stores only a day total.

## Change log

- **2026-10-04** — Implemented JSON-origin preflight, natural-key `409` detail, projected month capacity locking, worked-month contract interval validation for insertions, and compatibility-preserving legacy import behavior. Full suite: 542 passed, 100% coverage. API docs and Postman conflict example updated; release/CI pending.
- **2026-10-04** — Added endpoint tests for mixed insertion/explicit-update batches and commit rollback on unresolved blocks. Full suite now: 544 passed, 100% coverage.
- **2026-10-04** — Local quality gate passed: lint, dead-code, mypy, duplicate-code across the repository, and 544 tests with 100% coverage. Commit/push/CI/deployment remain pending explicit release authorization.
