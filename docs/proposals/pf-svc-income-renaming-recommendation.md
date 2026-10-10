# Recommendation: rename `pf-payroll` to `pf-svc-income`

Date: 2026-10-05
Status: approved
Level: L
Scope: Cross-repository/service identity rename and deployment migration.
Related artifacts: [investigation](../investigations/pf-svc-income-renaming.md) · [brief](pf-svc-income-renaming-brief.md) · [plan](pf-svc-income-renaming-plan.md)
Approved: 2026-10-05 by a0a11b7 (conversation)
Superseded-by: none

## 1. Executive recommendation

Approve a **phased identity rename** to `pf-svc-income` with these boundaries:

- rename the GitHub repository, local module folder, Python distribution, CI/CD service input, Artifact Registry repository, Cloud Run service, runtime service account, and current documentation identity;
- retain `src/payroll` for payroll-specific use cases and introduce a second `src/income` package for stable shared capabilities consumed by payroll, future independent bonuses, and honorarios;
- preserve all current `/payroll/*` HTTP routes because they are payroll-specific;
- rename the existing payroll-owned database objects to the approved `INC_*` vocabulary, including `PAY_PERIOD → INC_PAYROLL`, through a reversible `pf-db` migration;
- introduce `PF_INCOME_API_KEY` as the target service secret and cut over atomically; do not add a temporary application fallback to `PF_PAYROLL_API_KEY`;
- deploy the new Cloud Run service in parallel, validate it, then cut consumers over; retain the old service until rollback evidence and the agreed window expire;
- treat future off-cycle payments and honorarios as separate domain modules. Do not implement them as nullable additions to `INC_PAYROLL`.

This is an identity rename plus a physical schema rename and package-boundary change; it is not yet the implementation of independent bonuses or honorarios.

### Evidence update from GCP inventory 03 (2026-10-07)

The current `pf-payroll` service remains active and the target resources do not
exist. The rename still requires consumer inventory, URL reconciliation, schema
migration validation, least-privilege deployment approval, and explicit
authorization before execution.

Detailed security, IAM, backup, error-baseline, and observability findings are
registered separately as `IMP-001` through `IMP-007` in the ecosystem improvement
register. They are not repeated here; this recommendation records only the gates
that directly affect the rename cutover.

### Complementary validation decisions

- Use a bounded rollback window of 14 calendar days and at least one representative
  payroll flow, whichever is longer.
- Keep `/payroll/*` paths and synchronize `period_id → payroll_id` across route
  metadata, OpenAPI, DTOs, `docs/api.md`, Postman, and tests.
- Treat the current database and deployment identities as inputs to the separately
  tracked least-privilege work; do not copy broad permissions automatically.



## 2. Rejected alternatives

### A. Rename only the folder/repository

Rejected. It leaves `pyproject.toml`, package imports, Docker commands, GCP configuration, secrets, Postman, and operational docs inconsistent. That creates a split identity and future migration debt.

### B. Rename every occurrence of `payroll`

Rejected. `/payroll/*`, `PAY_*`, payroll use-case names, and historical proposal records describe real current domain concepts. Mechanical replacement would break clients, damage auditability, and erase the distinction between service identity and payroll subdomain.

### C. Do not rename the database objects

Rejected. The user explicitly decided that the current `PAY_*` objects must move to an `INC_*` vocabulary, with `PAY_PERIOD → INC_PAYROLL`. The migration is therefore in scope, but it must be a reversible table-rename migration owned by `pf-db`, not a drop/recreate or data rewrite.

### D. Rename every Python use case into a generic income package

Rejected. The current code shows real payroll-only behavior: contracts, payroll periods, AFP, health, unemployment insurance, complementary insurance, monthly employment tax, payslip PDFs, and payroll reconciliation. Extract only generic capabilities with no payroll repository/domain dependency.

### E. Keep the old Cloud Run service and only change GitHub naming

Rejected as an incomplete deployment identity migration. The repository and CI would say `pf-svc-income` while production still exposes a payroll-named service and service account. A bounded compatibility period is acceptable; permanent split identity is not.

### F. Change `/payroll` to `/income` during this rename

Rejected. Route migration is a public API change and must be separately designed, versioned, documented, and consumer-tested.

## 3. Target identity contract

| Surface | Target | Compatibility policy |
|---|---|---|
| GitHub repo | `mrturo-pf/pf-svc-income` | GitHub rename/redirect; update remotes and links |
| Local folder | `modules/pf-svc-income` | Update all relative paths |
| Python distribution | `pf-svc-income` | New build metadata |
| Python package layout | `src/payroll` retained for payroll-specific flows plus `src/income` for shared capabilities | `payroll` remains; only generic modules move |
| Cloud Run | `pf-svc-income` | Parallel deploy, then cutover |
| Artifact Registry | `pf-svc-income` | New repository; old retained for rollback/retention policy |
| Service account | `pf-svc-income@PROJECT_ID.iam.gserviceaccount.com` | New principal; grant exact required roles; old bounded retention |
| API key secret | `PF_INCOME_API_KEY` | Atomic cutover; no application fallback to `PF_PAYROLL_API_KEY` |
| Shared DB URL | `PF_DATABASE_URL` | Unchanged |
| Rates URL/key | `PF_RATES_URL`, `PF_RATES_API_KEY` | Unchanged |
| HTTP routes | `/payroll/*` | Unchanged in this change |
| Tables/views | `INC_*`, including `INC_PAYROLL` | Reversible `pf-db` migration from current `PAY_*` objects |
| Postman identity | `pf-svc-income-url`, `pf-svc-income-api-key`, folder `pf-svc-income` | Update collection/env; old variables may be temporary aliases |

### Source package split

Keep payroll-specific behavior in `payroll`. Add `income` only for capabilities that future independent bonuses and honorarios can consume without knowing payroll persistence.

Initial candidates based on current code:

- `domain/quantizers.py` → `income.domain`;
- `domain/deflation.py` → `income.domain`;
- exchange-rate/index resolution helpers → `income.application`;
- a narrowed market-data port → `income.application.ports`;
- generic money/currency/index DTOs only after field-level review.

Keep these in `payroll` initially:

- payroll import/reconciliation and `PayrollRepository`;
- payroll-period queries and exports;
- employer/contract maintenance;
- AFP, health, unemployment and complementary insurance;
- Chilean monthly employment tax;
- payslip PDF extraction and templates.

`DeflateAmounts` should initially remain a payroll adapter because it reads `PayrollSummaryDTO`; later it can depend on an income read port. `ComputeIncomeTax` remains payroll-specific until the honorarios withholding rules are designed separately.

Dependency direction must be one-way:

```text
payroll → income
future bonus/honorarios → income
income ↛ payroll infrastructure
```

### Database migration

The migration owns the following approved physical-name map. The ecosystem convention is proposed to allow up to 30 characters for table/materialized-view names; PostgreSQL's standard limit remains 63 bytes. The longest target below is `INC_PAY_PDF_TEMPLATE_FIELD` at 26 ASCII characters.

```text
PAY_PENS_INST          → INC_PENS_INST
PAY_HLTH_INST          → INC_HLTH_INST
PAY_PENS_PLAN          → INC_PENS_PLAN
PAY_HLTH_PLAN          → INC_HLTH_PLAN
PAY_CNTRB_CAP          → INC_CNTRB_CAP
PAY_COMP_PROV          → INC_COMP_PROV
PAY_COMP_PLAN          → INC_COMP_PLAN
PAY_EMPLOYER           → INC_EMPLOYER
PAY_EMP_CONT           → INC_EMP_CONT
PAY_PERIOD             → INC_PAYROLL
PAY_PRD_HLTH           → INC_PAY_PRD_HLTH
PAY_PRD_COMP           → INC_PAY_PRD_COMP
PAY_CONCEPT            → INC_PAY_CONCEPT
PAY_ITEM               → INC_PAY_ITEM
PAY_PDF_TEMPLATE       → INC_PAY_PDF_TEMPLATE
PAY_PDF_TEMPLATE_FIELD → INC_PAY_PDF_TEMPLATE_FIELD
PAY_MV_SUMARY          → INC_MV_PAY_SUMMARY
```

All targets are uppercase and use the `INC_` service prefix. `INC_MV_PAY_SUMMARY` intentionally makes the payroll domain explicit and corrects `SUMARY` to `SUMMARY`; it has 18 ASCII characters and fits the proposed 30-character limit. Index and constraint names must be explicit, unique within the schema, and below PostgreSQL's 63-byte limit. No migration or generator should rely on automatic identifier truncation.

The migration also includes the approved semantic column renames:

```text
PAY_PRD_HLTH.period_id     → INC_PAY_PRD_HLTH.payroll_id
PAY_PRD_COMP.period_id     → INC_PAY_PRD_COMP.payroll_id
PAY_ITEM.period_id         → INC_PAY_ITEM.payroll_id
PAY_MV_SUMARY.period_id    → INC_MV_PAY_SUMMARY.payroll_id
PAY_PERIOD.period_year     → INC_PAYROLL.accrual_year
PAY_PERIOD.period_month    → INC_PAYROLL.accrual_month
PAY_MV_SUMARY.period_year  → INC_MV_PAY_SUMMARY.accrual_year
PAY_MV_SUMARY.period_month → INC_MV_PAY_SUMMARY.accrual_month
```

Primary-key columns remain `id` and all `BIGSERIAL`/sequence values are preserved. No new keys, alternate keys, audit columns, nullable honorarios fields, or generic income ledger are introduced by this rename.

The migration must explicitly verify or rename dependent PK/FK/unique/check constraint names, indexes, sequences and sequence ownership, materialized-view columns/definition, seeds, restore scripts, inspection queries, and ORM mappings. PostgreSQL does not make it safe to assume every dependent object name follows a table/column rename automatically.

Because the old service cannot safely operate against renamed table and column names, the recommended cutover uses a short maintenance boundary: stop old traffic, apply the `pf-db` migration, deploy the new ORM/package, run smoke tests, and enable new traffic. A zero-downtime compatibility bridge is a separate design and is not assumed here.


Payroll routes remain under `/payroll/*` because they represent payroll-specific operations. The detail route should be exposed/documented as `GET /payroll/{payroll_id}`, not `/payroll/{id}`. The concrete URL remains `/payroll/481`; only the OpenAPI/path-parameter name, handler variable, command fields, DTOs, repository port fields, and documentation change.

The entity represented by `INC_PAYROLL` is `Payroll`, not `PayrollPeriod`. Recommended application vocabulary:

```text
PayrollPeriodDetailDTO       → PayrollDetailDTO
PayrollPeriodRangeDTO        → PayrollRangeDTO / PayrollDateRangeDTO
PayrollPeriodRangeContextDTO → PayrollRangeContextDTO
ImportedPayrollPeriodDTO     → ImportedPayrollDTO
period_id                    → payroll_id
periods[]                    → payrolls[] when the collection contains payroll entities
```

`period_year` and `period_month` need a focused field decision. Prefer `accrual_year`/`accrual_month` when they mean the month worked/earned, or `payroll_year`/`payroll_month` if that is the official domain vocabulary. Keep `payment_date` separate.

### 5.1 GCP migration design

### Before implementation

1. Inventory consumers, current URLs, repository configuration, and the existing
   deployment contract.
2. Confirm the approved package, schema, API, Postman, and rollback decisions.
3. Ensure the registered external gates required for cutover have an owner and
   an explicit decision.

### Migration sequence

1. Prepare the target repository, package, schema migration, and immutable build.
2. Create the target service resources only after explicit authorization.
3. Deploy the new service with the approved identity and cost-conscious scaling.
4. Run health, authentication, database, API, OpenAPI, and representative synthetic
   smoke tests.
5. Update Postman and known consumers while preserving `/payroll/*` routes.
6. Monitor the approved rollback window and remove old resources only after explicit
   authorization.

Security, IAM, backup, error-baseline, and observability designs are tracked by
`IMP-001` through `IMP-007`; this recommendation consumes their approved outcomes
instead of reproducing their detailed designs.

## 6. Secret cutover strategy

The API key transition is atomic:

- create `PF_INCOME_API_KEY` in Secret Manager;
- deploy the new service configured only with `PF_INCOME_API_KEY`;
- update clients/Postman to use the new key variable;
- retain `PF_PAYROLL_API_KEY` access for the old service until the rollback window is explicitly closed; revoke/remove old-secret access only during approved cleanup;
- do not implement fallback logic between the old and new names;
- if cutover fails, rollback uses the old service and its already-known old secret as an operational rollback path, not as application-level dual-secret compatibility.

The old secret must not be deleted until rollback is explicitly closed, but the new application must not accept both names.

## 7. Cross-repository sequencing and ownership

| Order | Repository | Work | Gate |
|---:|---|---|---|
| 1 | `pf-base` | Register artifacts and coordinate decisions | User approves recommendation |
| 2 | `pf-common` | Update reusable workflow docs/examples and path-based dependency sync | Shared-tool tests pass |
| 3 | `pf-db` | Add and validate the reversible `PAY_*` → `INC_*` migration plus ownership docs | Migration upgrade/downgrade and synthetic existing-data tests pass |
| 4 | `pf-svc-income` | Rename repository/folder/package/build/workflows/docs/tests | Full quality gate passes |
| 5 | `pf-base` | Update Postman, architecture, root docs, environments | JSON/OpenAPI/reference checks pass |
| 6 | GCP/GitHub | Prepare parallel resources and deploy | Explicit deployment authorization and smoke tests |
| 7 | All affected repos | Update links/remotes/runbooks and monitor rollback window | Cutover/release record complete |

The repository rename itself is an external GitHub operation and must be performed only with explicit authorization. Each repository remains independently committed and pushed.

The database migration must precede traffic for code that references `INC_*`. `pf-db` must release and apply the migration first; only then may the renamed service deploy code that uses the new ORM table names.

## 8. Validation and acceptance matrix

| Area | Validation | Acceptance |
|---|---|---|
| Identity | Search current repos and generated docs | No unintended current-reference `pf-payroll` remains; historical references are intentional and annotated |
| Python | Import check for `payroll` plus `income` and architecture tests | Payroll imports remain valid; extracted income package does not import payroll infrastructure |
| Build | `make check`, Docker build, container `/health` | Existing service quality gate passes |
| DB | `alembic upgrade head` and `downgrade` in fresh DB; migration against synthetic existing data; catalog/ORM assertions | All `INC_*` objects and approved column names exist; rows/IDs/PKs/FKs/indexes/sequences/view semantics survive; rollback works |
| API | Generated `/openapi.json` versus `docs/api.md` | Existing `/payroll/*` route family remains; intentional payroll DTO/field vocabulary changes are documented and synchronized |
| Postman | Parse collection/environment and run health/auth smoke tests | New variables/folder/base URL work; no old API-key alias is used by the new application |
| CI | PR workflows in every changed repository | All required checks pass |
| GCP | Describe/list resources, IAM and Secret Manager checks | New service has least-privilege access and scale settings |
| Runtime | `/health`, protected read, PDF preview/import validation with synthetic data | New service matches old behavior for representative cases |
| Local migration probe | Fresh PostgreSQL 16, Alembic `0016`, synthetic employer/payroll, full table/column rename in one transaction, FK/materialized-view checks, rollback | Rows/IDs/FKs/columns preserved; rollback left zero synthetic rows and original `PAY_*` schema. This must become a hand-written `pf-db` migration and run against representative existing data. |
| HTTP contract comparison | 27 route definitions vs `docs/api.md` vs 28 Postman requests | Docs cover application routes plus framework docs; Postman covers functional operations with literal-ID examples. Synchronize `period_id → payroll_id`; compare generated OpenAPI before release. |
| GitHub continuity | Repository, environments, workflows, recent runs, branch protection | Metadata visible; `production` has required reviewers; `main` unprotected; secret-list APIs returned HTTP 500. Retry with working permissions/API before repository rename. |
| Rollback | Maintenance-boundary rollback procedure | Old application/schema combination is restored only through the tested migration downgrade or a separately approved compatibility bridge |

## 9. Rollout and rollback

Rollback has two layers:

1. **Application/GCP rollback:** route clients back to the old Cloud Run service and old secret during the evidence window.
2. **Schema rollback:** execute the tested `pf-db` downgrade only if the new service is stopped and no incompatible post-migration schema change has occurred.

Because the table rename is physical, the old application cannot safely run against the renamed schema unless it is deployed with compatibility ORM names or the migration is downgraded. The rollout therefore requires either a short maintenance boundary for the schema cutover or a compatibility migration strategy. The recommendation is to choose the compatibility strategy before implementation and not pretend a database rename is traffic-free.

The rollback window ends only after the following evidence is collected:

- all known consumers use the new service and new API-key variable;
- representative payroll imports, reads, exports, PDF preview, and reference-data requests succeed;
- new service writes and reads `INC_*` correctly;
- old-service compatibility behavior is explicitly tested or the old service is retired from rollback scope;
- no elevated 4xx/5xx, latency, database, or `pf-rates` errors remain for the agreed observation period;
- user explicitly authorizes old resource cleanup.

## 10. Cost impact

- **Infrastructure:** temporary duplication of one Cloud Run service, Artifact Registry repository, and service account during the rollback window. With `min-instances=0`, Cloud Run compute cost is limited to traffic; the cheapest viable approach is a short, explicit blue/green window rather than permanent dual service operation.
- **Rollback:** 14 calendar days minimum, extended until one representative payroll flow has completed successfully; explicit user approval is required before cleanup.
- **Storage:** temporary duplicate container image storage. Retain only the minimum immutable tags needed for rollback and apply the existing retention policy.
- **Scanning:** no new paid scanning; retain Trivy and disabled Artifact Registry vulnerability scanning.
- **API/provider usage:** no expected change; the new service calls the same `pf-rates` endpoints and shared database.
- **Data operations:** one reversible metadata/schema migration is required. It should rename objects in place, preserve rows and IDs, and avoid copying the database. The cheapest viable approach is an online/in-transaction rename where PostgreSQL locking and application compatibility permit; otherwise use a short maintenance boundary rather than a duplicate database.
- **Engineering/operations:** additional IAM, secret, monitoring, and cutover work is the main cost. A repository-only rename would be cheaper short term but rejected because it leaves production identity inconsistent.

## 11. Decision gate

Before implementation or resource creation, require:

- approval of the rename depth, target identity, package split, `INC_*` map, and
  retained `/payroll/*` routes;
- validation of the hand-written `pf-db` migration and rollback strategy;
- consumer, URL, OpenAPI, API documentation, and Postman reconciliation;
- explicit authorization for repository changes, resource creation, secret changes,
  commits, pushes, and deployment;
- resolution or explicit acceptance of the registered external gates that affect
  cutover (`IMP-001` through `IMP-007`).

The current `pf-db` 14-character naming policy remains in force until the separate
`IMP-008` identifier-length work is completed. The rename recommendation does not
activate the proposed 30-character convention.
