# Employers and Employment Contracts Maintenance Implementation Plan

**Date:** 2026-10-04
**Status:** in progress
**Level:** L
**Scope:** `pf-payroll` employer/contract maintenance and coordinated `pf-db` integrity
**Related artifacts:** [`employers-contracts-maintenance-brief.md`](employers-contracts-maintenance-brief.md), [`employers-contracts-maintenance-recommendation.md`](employers-contracts-maintenance-recommendation.md)
**Approved:** 2026-10-04 by Arturo (conversation)
**Superseded-by:** none

## Slices

| Slice | Description | Status | Exit criteria |
| --- | --- | --- | --- |
| 1 | DTOs, ports, use cases, dependencies | in progress | Application boundaries compile and are covered. |
| 2 | SQLAlchemy repositories and transactional guards | planned | Employer/contract CRUD, blockers, overlap, and atomic batches work. |
| 3 | HTTP routes and OpenAPI contract | planned | CRUD endpoints expose ID-based relations and stable errors. |
| 4 | `pf-db` migration/DDL documentation | planned | Global non-overlap constraint upgrades/downgrades cleanly. |
| 5 | Tests and documentation/Postman | planned | Full quality gate and contract docs agree. |
| 6 | Release | planned | Commit, push, CI, approval, deployment, and smoke evidence recorded. |

## Decisions

- Employer and contract collection resources are separate.
- POST collection bodies are atomic batches; ID presence selects create/update.
- Contract references use `employer_id`, never employer name.
- Contract interval overlap is global across employers.
- Payroll deletion guards use worked-calendar-month overlap, not payment date alone.
- Exact daily allocation is out of scope because `PAY_PERIOD` stores only month and total worked days.

## Change log

- **2026-10-04** — Added initial employer/contract maintenance DTOs, repository port methods, use cases, transactional dependency wiring, SQLAlchemy maintenance mixin, and HTTP routes. OpenAPI exposes `GET/POST/DELETE /payroll/employers` and `/payroll/contracts`; quality checks pass, but repository tests, `pf-db` exclusion migration, API docs/Postman, and full coverage remain before release.
- **2026-10-04** — Added focused use-case, route, dependency, and repository tests. `make test-cov` now passes with 567 tests and 100% coverage. `pf-db` Ruff, Python compilation, and diff checks pass; Docker-backed migration execution remains unavailable locally.
