# Usage and impact of `pf-payroll` operation endpoints

**Last updated:** 2026-10-03
**Status:** updated after removing these three HTTP and CLI surfaces.
**Scope:** complete `pf-*` ecosystem (`pf-base`, `pf-payroll`, `pf-rates`, `pf-sheets`, `pf-db`)

## Current status

This document replaces the 2026-10-02 snapshot. The previous recommendation became
obsolete after its partial implementation.

The current HTTP operation surface is:

| Endpoint | Current HTTP surface | Current CLI/use-case surface | Status |
| --- | --- | --- | --- |
| `POST /payroll/{period_id}/assign-plans` | No | No | Removed |
| `POST /payroll/{period_id}/compute-contributions` | No | No; internal service preserved | Wrapper removed |
| `POST /payroll/{period_id}/compute-tax` | No | No; internal use case preserved | Adapter removed |
| `POST /payroll/{period_id}/review` | No | No | Removed |
| `POST /payroll/{period_id}/deflate` | No | No dedicated surface; related internal logic remains | Removed as HTTP surface |

The source of truth for routes is:

```text
modules/pf-payroll/src/payroll/interfaces/api/routes/payroll.py
```

The Postman collection and `docs/api.md` must match this table.

## Final exposed-surface validation

On 2026-10-03, the generated application OpenAPI routes were automatically compared with
`docs/api.md` and with the `pf-payroll` requests in the Postman collection.

Result:

- OpenAPI: **19 operations**, including `/health`.
- `docs/api.md`: exact match for all documented operations; FastAPI internal routes
  (`/docs`, `/redoc`, `/openapi.json`) are not part of the functional inventory.
- Postman: **19 `pf-payroll` requests**, covering the same functional endpoints; some
  requests use concrete example IDs for `{period_id}` and `{template_id}`.
- Differences involving removed operations: **none**.

The three removed operations do not appear in OpenAPI, `docs/api.md`, or Postman:

```text
POST /payroll/{period_id}/assign-plans
POST /payroll/{period_id}/compute-contributions
POST /payroll/{period_id}/compute-tax
```

The original analysis recommended removing `assign-plans`, `compute-contributions`,
`compute-tax`, and `deflate` while keeping `review`. The implementation did not follow
that recommendation literally:

- `assign-plans` was removed completely, including its use case, DTOs, repository method,
  CLI command, route, tests, and Postman request.
- `compute-contributions` lost its HTTP/CLI wrapper. `ContributionComputationService` and
  its calculation DTOs were preserved because import post-processing uses them.
- `compute-tax` lost its HTTP/CLI adapters. `ComputeIncomeTax` was preserved because
  `ProcessImportedPayrollPeriods` executes it automatically.

Therefore, the previous document must be treated as historical evidence, not as a
description of the current system.

## Historical production evidence

The log window reviewed on 2026-10-02 showed:

| Endpoint | Observed requests | Result |
| --- | ---: | --- |
| `assign-plans` | 0 | No observed requests |
| `compute-contributions` | 0 | No observed requests |
| `compute-tax` | 0 | No observed requests |
| `review` | 2 | 1 × `200`, 1 × `404` |
| `deflate` | 1 | 1 × `404` |

The successful `review` request was:

```text
POST /payroll/548/review → 200
User-Agent: PostmanRuntime/2.7.0
Timestamp: 2026-09-28T19:39:55Z
```

That traffic explains why the original recommendation proposed keeping `review`, but the
subsequent decision was to remove that workflow. Historical evidence must not be confused
with the current HTTP surface.

## Consumers inside the ecosystem

No calls from `pf-rates`, `pf-sheets`, or `pf-db` to these routes were found.

- `pf-rates` does not consume `pf-payroll`.
- `pf-sheets` uses the CSV export from `pf-rates` and its local `VALUES` sheet.
- `pf-db` contains DDL, migrations, and seeds; it does not consume HTTP.
- CLI commands and internal `pf-payroll` flows are not HTTP consumers.

The absence of internal consumers remains true, but it does not by itself prove that an
administrative route had no manual use outside the repositories.

## Current endpoint evaluation

### `assign-plans`

It was removed from HTTP, CLI, Postman, and the application layer. Import processing does
not use this removed use case; it assigns complementary plans through
`ComplementaryInsuranceService`, which is a separate operation and remains active.

**Current status:** completely removed.

### `compute-contributions`

The HTTP/CLI wrapper was removed. The reusable logic was not removed:
`ContributionComputationService` is still instantiated by
`ProcessImportedPayrollPeriods` during automatic import processing.

**Current status:** HTTP/CLI adapters removed; internal service preserved.

### `compute-tax`

The HTTP and CLI adapters were removed. `ComputeIncomeTax` remains an internal use case
and is instantiated and executed by `ProcessImportedPayrollPeriods` when the imported
period meets its prerequisites.

**Current status:** HTTP/CLI adapters removed; internal use case preserved.

### `review`

It no longer exists as an HTTP route or CLI command. The review workflow and contracts
associated with `PAY_PERIOD.status` were also removed.

The historical `200` request is recorded only as migration context. It must not be added
back to Postman or `docs/api.md` without a new design decision.

**Current status:** intentionally removed.

### `deflate`

It no longer exists as an HTTP endpoint or Postman request. The internal
`DeflateAmounts` use case remains because other application flows still use related logic,
although it has no dedicated HTTP interface.

There is no evidence of a successful historical HTTP use; the only observed request ended
with `404`.

**Current status:** removed from HTTP; do not remove the internal use case without a
separate call-site review.

## Current recommendation

The current decision should not be described as “remove four endpoints and keep `review`.”
That recommendation was already partially implemented in a different form.

The previous recommendation is superseded by the implementation that was completed:

1. remove `assign-plans` completely;
2. remove the `ComputeContributions` wrapper while preserving
   `ContributionComputationService`;
3. remove only the HTTP/CLI adapters for `compute-tax` while preserving `ComputeIncomeTax`;
4. preserve automatic import post-processing;
5. do not reintroduce Postman requests for these operations.

## Current consistency checklist

- `review` must not appear as an endpoint in `docs/api.md` or Postman.
- `deflate` must not appear as an endpoint in `docs/api.md` or Postman.
- `assign-plans`, `compute-contributions`, and `compute-tax` must not appear as endpoints
  or CLI commands in `docs/api.md` or Postman.
- `ContributionComputationService` must remain covered by tests and used by automatic
  import processing.
- `ComputeIncomeTax` must remain covered by tests and used by automatic import processing.
- Historical log evidence remains context only, not the current inventory.

## Conclusion

This document now describes the verified state after removing the three HTTP/CLI surfaces.
The analyzed operation routes do not appear in current OpenAPI or Postman; only internal
services/use cases required by the import flow remain.

The current conclusion is: the three operations no longer have HTTP or CLI surfaces.
`assign-plans` was removed entirely. The internal contribution service and internal tax
use case remain because the automatic import flow requires them. The HTTP `review` and
`deflate` surfaces also remain removed.
