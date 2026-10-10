# Plan: rename `pf-payroll` to `pf-svc-income`

Date: 2026-10-05
Status: planned
Level: L
Scope: Cross-repository/service identity rename and GCP migration.
Related artifacts: [investigation](../investigations/pf-svc-income-renaming.md) · [brief](pf-svc-income-renaming-brief.md) · [recommendation](pf-svc-income-renaming-recommendation.md)
Approved: 2026-10-05 by a0a11b7 (conversation)
Superseded-by: none

> This is a living implementation record. It is intentionally not authorized for execution yet. No implementation, repository rename, secret rotation, push, or deployment has occurred.

## 1. Status and gates

- [x] User decisions recorded.
- [x] Recommendation approved explicitly.
- [x] GitHub repository/workflow/environment inventory completed read-only.
- [x] Existing Cloud Run, Artifact Registry, Secret Manager, IAM, Scheduler, Logging, monitoring, and networking inventory completed to the extent permitted by the active account.
- [x] HTTP route, service API documentation, and Postman semantic comparison completed.
- [x] Local synthetic `pf-db` rename probe completed with transactional rollback and no persisted synthetic rows.
- [x] GitHub workflow/environment/history inventory completed read-only; repository and environment secret names were reviewed without reading values.
- [x] Environment-scoped secret inventory completed.
- [ ] External register gates `IMP-001` through `IMP-007` are resolved or explicitly
  accepted for cutover.
- [ ] Cloud Run URL policy and Postman target choice approved; neither advertised
  URL is assumed stale without a decision.
- [ ] Repository rename authorization received.
- [ ] Implementation slices completed and validated.
- [ ] Cross-repository commits/pushes authorized separately.
- [ ] Deployment approval explicitly authorized.
- [ ] Rollback window of 14 calendar days (or until one representative payroll flow
  completes, whichever is longer) approved.

The lifecycle states intentionally describe different artifacts: the ecosystem
index tracks the overall investigation as `investigating`; the brief and
recommendation record an approved design; and this plan remains `planned` because
implementation is blocked and not authorized. These statuses are compatible: the
recommendation is approved while execution remains pending explicit gates.

## 1.1 Latest evidence and blockers (2026-10-07)

Inventory confirms that the current `pf-payroll` service remains active and the
`pf-svc-income` target resources do not exist. Rename-specific blockers are
consumer inventory, URL cutover, schema migration validation, package-boundary
review, and explicit authorization.

Unrelated security, IAM, backup, error-baseline, and observability findings are
registered as `IMP-001` through `IMP-007` in the ecosystem improvement register.
They may provide release gates, but their detailed remediation does not belong in
this rename plan.

## 1.2 Required execution order

This order is normative for the proposal. Phase 1 is read-only. Approval gates are
recorded before Phase 3 mutations; external follow-ups remain in the register.


### Phase 1 — Safe investigation, without mutations

1. Identify known consumers through authorized logs, Postman, repositories,
   workflows, jobs, and configuration.
2. Reconcile current and historically advertised Cloud Run URLs, including the
   Postman-configured URL, without assuming that either is stale.
3. Validate the approved API, package, schema, migration, and rollback contracts.
4. Record any unrelated finding in the ecosystem improvement register instead of
   expanding this plan.

### Phase 2 — Decisions and approvals

Approve the rename-specific package, schema, API, consumer, cutover, and rollback
contracts. Resolve or explicitly accept the registered external gates before Phase 3.

### Phase 3 — Controlled changes, with explicit authorization

1. Apply only approved consumer, repository, package, schema, and Postman changes.
2. Apply the reversible `pf-db` migration before traffic uses the new ORM names.
3. Deploy `pf-svc-income` with immutable identity and approved settings.
4. Execute representative synthetic smoke tests and monitor the approved rollback
   window.
5. Handle external security and operations gates through their registered entries.

No target resource, database migration, consumer cutover, or deployment is
authorized merely because it appears in this plan.

## 1.3 Phase 2 decision package

The following decisions are prepared from the Phase 1 evidence. They are proposals for explicit approval, not executed changes. No credential rotation, IAM mutation, resource creation, schema migration, consumer update, or deployment is authorized by this section alone.

### Out-of-scope operational and security follow-ups

The following findings are not part of the rename design. They are registered in
[`ECOSYSTEM-IMPROVEMENT-REGISTER.md`](../../../../docs/proposals/ECOSYSTEM-IMPROVEMENT-REGISTER.md)
and remain separate workstreams:

- `IMP-001` — Scheduler credential exposure and incident response;
- `IMP-002` — Neon credential, backup, restore, and sensitive-dump hardening;
- `IMP-003` — least-privilege database runtime roles;
- `IMP-004` — `pf-rates` access to `PF_DATABASE_URL`;
- `IMP-005` — deployer/runtime identity separation and WIF;
- `IMP-006` — ecosystem HTTP error baseline;
- `IMP-007` — Cloud Run observability and capacity standards.

The rename remains blocked by the directly relevant gates from these workstreams,
but their detailed investigation, remediation, and reusable standards do not belong
in this plan. Keep only the gate result and a link here.

### Schema maintenance window

**Proposal:** use a short maintenance boundary rather than a zero-downtime physical rename.

Before the window:

- backup and restore readiness from `IMP-002` is confirmed;
- the deterministic contract parity suite is run against `pf-payroll`;
- Postman target variables are prepared without changing active consumers;
- if startup does not validate schema, the new image may be deployed before the window with no traffic.

During the window:

1. stop known consumers and isolate old traffic using an approved reversible mechanism;
2. wait at least the request timeout (`300s`) for in-flight requests to drain;
3. capture the pre-migration backup and schema/row-count snapshot;
4. apply the reversible `pf-db` migration;
5. recreate `INC_MV_PAY_SUMMARY` with `payroll_id`, `accrual_year`, and `accrual_month`;
6. run catalog, row-count, and aggregate assertions;
7. deploy or activate the compatible service image;
8. run Go/No-Go smoke tests before enabling consumers.

Maximum duration and abort criteria must be approved before execution. Do not use `--ingress=internal` as an automatic step until the invoker/ingress model and rollback procedure are confirmed; it is a candidate isolation mechanism, not an executed instruction.

Status: pending `IMP-002` backup/restore readiness and operational approval.

### Rollback window

**Proposal:** retain rollback capability for **14 calendar days and until one representative monthly payroll flow completes**, whichever is longer. Define the flow as import, review/summary, and export using synthetic or explicitly approved operational data.

During the window:

- retain the old service, image, account, and `PF_PAYROLL_API_KEY`;
- retain the old account's access to the old secret until closure;
- keep the old service at scale-to-zero and isolated from normal traffic;
- freeze `pf-db` migrations touching `INC_*` objects and dependencies;

Rollback is not merely redirecting traffic: stop the new service, run the tested schema downgrade, verify the original `PAY_*` schema and `PAY_MV_SUMARY` columns, restore old-service access, and run the parity suite. New migrations during the window invalidate this downgrade path unless explicitly re-approved.

Status: pending approval of observation period, flow definition, and freeze policy.

### Contract parity and release acceptance

The rename must compare the old and new service for health, authentication, reads,
imports, PDF preview, exports, reference data, and approved error cases. The
reusable HTTP error taxonomy and observability baseline are tracked as `IMP-006`.
Cloud Run alerting, probes, capacity limits, and database connection-budget
standards are tracked as `IMP-007`.

For this rename, the acceptance gate is limited to:

- approved contract differences are documented;
- generated OpenAPI, `docs/api.md`, Postman, and route definitions agree;
- representative synthetic flows pass against the renamed service;
- no unexplained regression is introduced by the cutover.

### Database change freeze

**Proposal:** from cutover until rollback closure, do not merge migrations touching `INC_*` objects or dependencies. An emergency exception must explicitly restate or invalidate the downgrade path.

Status: pending approval.

### Decision 12 — Invocation and URL compatibility

Verify the target service's invocation policy and preserve the approved URL and
API-key cutover behavior. Do not assume either currently advertised URL is stale;
reconcile it with consumers and Postman before cutover. Broader IAM and ingress
standards are tracked through `IMP-005` and `IMP-007`.

Status: pending URL evidence and approval.

### Related database security work

The rename must not broaden database privileges or bypass the current `pf-db`
identifier policy. Runtime-role hardening is tracked as `IMP-003`; Neon credential,
backup, and restore hardening is tracked as `IMP-002`; and the identifier-length
policy is tracked as `IMP-008`. Those entries remain prerequisites or explicit
approval gates where the cutover requires them, but their detailed designs belong
in their own proposals.

### Phase 2 gate

Phase 3 remains blocked until the rename-specific decisions are approved and the
registered external gates that affect cutover (`IMP-001` through `IMP-007`) are
resolved or explicitly accepted. Preparing this package does not rotate
credentials, change IAM, create GCP resources, alter the database, update Postman,
or deploy a service.

## 2. Decisions to record before implementation

- [x] GitHub repository target is `mrturo-pf/pf-svc-income`.
- [x] Local folder target is `modules/pf-svc-income`.
- [x] Retain `payroll` for payroll-specific behavior and add a second shared package; recommended name `income`.
- [x] Runtime/API key target is `PF_INCOME_API_KEY`.
- [x] Existing `/payroll/*` HTTP routes remain; detail route is `/payroll/{payroll_id}`.
- [x] Existing `PAY_*` objects are renamed through `pf-db`; `PAY_PERIOD → INC_PAYROLL`.
- [x] Exact `INC_*` target map is approved, subject to migration implementation validation.
- [x] `PAY_MV_SUMARY` target is explicitly `INC_MV_PAY_SUMMARY` (18 ASCII characters), correcting `SUMARY` to `SUMMARY` and making the payroll domain explicit.
- [ ] Update `pf-db` naming documentation/validators from 14 to 30 characters before implementing the migration.
- [ ] Review all generated and explicit index/constraint names for uniqueness and a maximum of 63 bytes; no automatic truncation.
- [x] No custom domain/DNS behavior is involved.
- [x] API key migration is atomic: target `PF_INCOME_API_KEY`; no application fallback to `PF_PAYROLL_API_KEY`.
- [ ] Rollback exit criteria and observation period are approved.

## 3. Implementation slices

### Slice 0 — Close evidence gaps and freeze design decisions

Repositories/files:

- actual GCP resources and service configuration recorded;
- GitHub repository settings, secrets, environments, webhooks, branch protection;
- external consumer and log inventory;
- Postman workspace/environment inventory;
- database role/backup/monitoring reference scan;
- exact package split and dependency boundary;
- exact `INC_*` target map;
- ORM/DTO/route vocabulary migration (`period_id → payroll_id`, period-oriented DTOs → payroll-oriented DTOs);
- API-key atomic cutover;
- schema cutover/rollback compatibility strategy.

- Actual resources and consumers are recorded in the investigation or its dated change log.
- No secret values are copied into artifacts.
- Unknowns affecting cutover are either resolved or explicitly blocked.

### Slice 1 — Coordination metadata

Files:

- root `docs/proposals/INDEX.md`;
- this plan and related artifacts.

Exit criteria:

- Slug is registered with Level L, owner `pf-base`, affected repositories, status, and links.

### Slice 2 — Shared tooling preparation

Repository: `pf-common`.

Expected changes:

- workflow comments/examples mentioning `pf-payroll`;
- path-based dependency synchronization script and messages;
- README/reference paths;
- any allowlists or service-name assumptions.

Exit criteria:

- Shared workflow syntax and script tests pass.
- No behavior change for `pf-rates`.

### Slice 3 — Database migration and ownership documentation

Repository: `pf-db`.

Expected changes:

- add a reversible hand-written migration renaming the approved `PAY_*` objects to the `INC_*` map (maximum 30 characters), including `PAY_PERIOD → INC_PAYROLL` and the full PDF-template names;
- rename relationship columns from `period_id` to `payroll_id` and temporal columns from `period_year`/`period_month` to `accrual_year`/`accrual_month`;
- preserve all `id` PKs, `BIGSERIAL` sequences, monetary types, enums, nullability, defaults, rows and IDs;
- explicitly audit/rename PK/FK/unique/check constraint names, indexes, sequence ownership, materialized-view definition/columns, seeds, restore scripts and inspection SQL; keep explicit index/constraint names below PostgreSQL's 63-byte limit and do not rely on automatic truncation;
- use a short maintenance boundary unless a separately designed compatibility bridge is approved;

Exit criteria:

- migration upgrades and downgrades cleanly on a fresh database;
- migration runs against synthetic existing data without row/ID loss;
- all FKs, indexes, constraints, materialized-view semantics and seeds are verified;
- ORM/application code is not deployed against `INC_*` until this migration is available.

### Slice 4 — Service source identity and package split

Repository after rename: `pf-svc-income`.

Expected changes:

- rename repository/folder through approved GitHub/local procedure;
- update `pyproject.toml` distribution metadata to `pf-svc-income`;
- retain `src/payroll` for payroll-specific modules;
- add `src/income` for extracted generic capabilities only;
- move/re-home quantizers, generic deflation, market-data ports/resolution, and reviewed generic DTOs;
- keep payroll import/reconciliation, payroll repositories, employer/contracts, contributions, employment tax, PDF, and payroll exports in `payroll`;
- update only affected imports, package data, tests, diagrams, and Docker packaging;
- update ORM table names to the approved `INC_*` names;
- rename period-oriented DTOs to payroll-oriented DTOs (`PayrollPeriodDetailDTO → PayrollDetailDTO` and equivalent range/import DTOs);
- update `period_id` to `payroll_id` across commands, ports, DTOs and route handlers;
- update collection fields where they represent payroll entities (`periods[] → payrolls[]` where applicable);
- update the detail route metadata and handler to `/payroll/{payroll_id}` while preserving concrete URLs;
- resolve `period_year`/`period_month` naming as `accrual_*` based on the semantic review;
- update config naming and perform the atomic `PF_INCOME_API_KEY` cutover;
- preserve the `/payroll/*` route family;

Exit criteria:

- `payroll` imports remain valid.
- `income` imports successfully and does not import payroll infrastructure.
- Architecture/dependency tests enforce `payroll → income`, never the reverse.
- Full service tests and static-quality checks pass.
- Docker image starts and `/health` works against the migrated schema.

### Slice 5 — Service docs and operational configuration

Repository: `pf-svc-income`.

Expected changes:

- `README.md`, `AGENTS.md`, getting-started, development, deployment, database, API, workflow docs;
- workflow `repo_name`/`service_name` inputs;
- GCP setup comments and resource names;
- local environment examples and secrets README;
- operational rollback/runbook references.

Exit criteria:

- Docs distinguish service identity `pf-svc-income` from payroll route/domain vocabulary.
- No credentials or real personal data are added.

### Slice 6 — Root ecosystem, Postman, and architecture

Repository: `pf-base`.

Expected changes:

- root README/AGENTS current references;
- Postman collection folder and variables;
- local/GCP Postman environments with the new base URL after deployment evidence;
- architecture JSON sources, links, labels, generated HTML via the documented generator;
- root diagrams/index references.

Exit criteria:

- JSON parses.
- OpenAPI and Postman agree on current `/payroll/*` paths.
- Generated architecture output matches source.

### Slice 7 — GCP parallel resources and schema-aware deployment

Operations/GCP, only after explicit authorization.

Expected actions:

- create new Artifact Registry repository;
- create new service account and least-privilege IAM;
- create/populate new API-key secret without exposing the value;
- grant access to shared secrets;
- build/push immutable image tags;
- deploy Cloud Run service with existing cost/scaling policy.

Exit criteria:

- New resource names, revisions, traffic, IAM, secrets, region, min/max instances, and logs are recorded.
- `/health` and authenticated synthetic smoke tests pass.

### Slice 8 — Consumer cutover and rollback window

Expected actions:

- update known Postman/clients/configuration;
- verify no old service traffic remains except expected compatibility traffic;
- monitor logs, errors, latency, database behavior, and rates calls;
- verify the new service is running against `INC_*` after the migration;
- verify old-service rollback behavior before removing old schema compatibility;
- retain old service/resources for the approved evidence window.

Exit criteria:

- New service is authoritative for the intended consumers.
- Rollback to old service is tested/documented.
- No manual deployment gate was approved without explicit user authorization.

### Slice 9 — Cleanup and closure

Expected actions, only after approval:

- remove old secret access/old service account;
- delete or retain old Cloud Run service and Artifact Registry repository per retention decision;
- finalize docs and proposal status;
- record commits, workflow run IDs, deployment URL, smoke results, and follow-ups.

Exit criteria:

- No unresolved old identity references except intentional historical/compatibility references.
- Index status is `released`, `validated`, `blocked`, or `closed` with evidence.

## 4. Validation command checklist

Commands are examples and must be run from the correct repository after reading its current instructions:

- reference scan for `pf-payroll`, `PF_PAYROLL`, `src/payroll`, and old URLs;
- service `make check` and Docker build/health validation;
- `python -c` package import check;
- `alembic upgrade head` and `alembic check` in `pf-db` test environment;
- JSON parse checks for Postman and architecture files;
- generated OpenAPI versus `docs/api.md` comparison;
- CI workflow validation;
- authorized GCP `gcloud` describe/list checks;
- authorized GitHub `gh` workflow/run checks only after `source "$HOME/Documents/scripts/unset_proxies.sh"` and unsetting certificate overrides as required by repository instructions.

## 5. Change log

| Date | Status | Change | Reason/evidence |
|---|---|---|---|
| 2026-10-05 | planned | Created the Level L investigation, brief, recommendation, and living plan. | Workflow-compliant planning; no code or infrastructure mutation. |
| 2026-10-07 | investigating | Completed rename-relevant route, consumer, Postman, schema, and deployment-identity inventory. | Read-only evidence; target identity does not yet exist. |
| 2026-10-07 | validating | Completed the local synthetic schema-rename and rollback probe. | `pf-db` migration shape remains implementation work. |
| 2026-10-10 | blocked | Moved unrelated security, IAM, backup, error-baseline, and observability findings to `IMP-001` through `IMP-007`. | Scope control; detailed follow-ups belong in the ecosystem register. |


## 6. Release record

- Commits: pending
- Workflow run IDs: pending
- Deployment URL: pending
- Old-service rollback window: pending user decision
- Final smoke tests: pending
- Known blockers/follow-ups: pending

## 7. Final summary placeholder

To be completed after validation/release: what shipped, what did not, authoritative contract locations, old-resource disposition, and workflow/process improvements discovered.
