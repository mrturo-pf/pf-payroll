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
- [x] GitHub workflow/environment/history inventory completed read-only; secret preservation remains blocked by GitHub API HTTP 500.
- [ ] Environment-scoped secret inventory completed.
- [ ] Scheduler credential exposure from inventory 01 remediated and old artifacts redacted/removed.
- [x] External consumer and Cloud Logging inventory completed in reduced-summary form; raw logs excluded from the sanitized package.
- [x] Current Artifact Registry repository/image inventory completed; target repository confirmed absent.
- [ ] Stale Postman GCP URL reconciled with live Cloud Run URL.
- [ ] Classification of baseline `500`/`502`/`403`/`404`/`409`/`422` responses completed.
- [ ] Scheduler credential exposure remediated.
- [ ] Least-privilege IAM design approved; current broad service-account roles must not be copied blindly.
- [ ] Repository rename authorization received.
- [ ] Implementation slices completed and validated.
- [ ] Cross-repository commits/pushes authorized separately.
- [ ] Deployment approval explicitly authorized.
- [ ] Rollback window of 14 calendar days (or until one representative payroll flow completes, whichever is longer) approved.

## 1.1 Latest evidence and blockers (2026-10-07)

Inventory 03 confirms the existing service baseline but does not authorize implementation:

- current Cloud Run service `pf-payroll` remains healthy at 100% traffic on revision `pf-payroll-00056-lgk`;
- target Artifact Registry, service account, and `PF_INCOME_API_KEY` do not exist;
- current runtime identity has broad project roles: Artifact Registry writer, Cloud SQL client, Service Account User, Cloud Run Admin, and Secret Manager accessor;
- no VPC connector, custom DNS zone, forwarding rule, alert, dashboard, log metric, or uptime check was found in the collected outputs;
- Cloud Logging confirms real Postman/curl consumers and a baseline containing `500`, `502`, `403`, `404`, `409`, and `422` responses;
- the first inventory exposed a Scheduler credential and requires separate rotation/remediation;
- `pf-rates` currently has access to `PF_DATABASE_URL` and `PF_RATES_API_KEY`; the database-secret access requires justification before reproducing IAM.

Resource creation, secret rotation, repository rename, pushes, and deployment remain explicit authorization gates.

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
| 2026-10-05 | investigating | Retried Artifact Registry, Cloud Logging, and Secret Manager after `unset_proxies` plus clearing `SSL_CERT_FILE`/`_SSL_CERT_FILE`; all remained blocked by VPC Service Controls. Cloud Run remained readable. | Proxy/certificate environment is not the blocker; requires approved VPC-SC context or GCP administrator assistance. |
| 2026-10-05 | planned | Created Level L investigation, brief, recommendation, and living plan. No code or infrastructure changed. | User requested investigation and workflow-compliant planning. |
| 2026-10-07 | investigating | Read-only GCP inventory completed and analyzed. Confirmed current Cloud Run, Artifact Registry, Secret Manager, IAM, Scheduler, Logging, monitoring, and networking baseline; target resources do not exist. | Dated operator-provided GCP inventory evidence; no GCP mutation. |
| 2026-10-07 | blocked | Earlier inventory exposed a literal Scheduler credential. Rotation and artifact redaction are required before sharing or closure. | Inventory 01 security finding; inventory 03 deliberately excludes headers and bodies. |
| 2026-10-07 | investigating | Compared 27 route definitions, `docs/api.md`, and 28 Postman requests. Functional coverage is present; parameterized contract rename remains synchronized-change work (`period_id` → `payroll_id`). | Static route extraction and JSON collection comparison. |
| 2026-10-07 | validating | Fresh local PostgreSQL 16 reached Alembic `0016`; synthetic full rename probe preserved IDs/FKs/materialized-view columns and rolled back with zero synthetic rows remaining. | Local `pf-db` Docker database; no production connection or mutation. |
| 2026-10-07 | blocked | GitHub secret-list endpoints returned HTTP 500. Environments/workflows/history are visible, but secret-name preservation remains unverified. | Read-only `gh api`/`gh secret list`; no secret values requested. |

## 6. Release record

- Commits: pending
- Workflow run IDs: pending
- Deployment URL: pending
- Old-service rollback window: pending user decision
- Final smoke tests: pending
- Known blockers/follow-ups: pending

## 7. Final summary placeholder

To be completed after validation/release: what shipped, what did not, authoritative contract locations, old-resource disposition, and workflow/process improvements discovered.
