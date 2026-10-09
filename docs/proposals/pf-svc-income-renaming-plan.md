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
- [ ] Scheduler credential exposure from inventory 01 remediated and old artifacts redacted/removed.
- [x] External consumer and Cloud Logging inventory completed in reduced-summary form; raw logs excluded from the sanitized package.
- [x] Current Artifact Registry repository/image inventory completed; target repository confirmed absent.
- [ ] Cloud Run URL policy and Postman target choice approved; both currently advertised URL formats are valid and must not be labeled stale without a decision.
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

## 1.2 Required execution order

This order is normative for the proposal. Phase 1 is read-only. Phase 2 records approvals. Phase 3 contains mutations and cannot start until the preceding phases are complete.

### Phase 1 — Safe investigation, without mutations

1. Complete the environment-scoped GitHub secret inventory using names and metadata only.
2. Review, redact, or remove artifacts containing credentials, raw logs, or sensitive data.
3. Identify consumers through authorized logs, Postman, repositories, workflows, jobs, and configuration.
4. Reconcile current and historical Cloud Run URLs, including the stale Postman URL.
5. Classify `500`, `502`, `403`, `404`, `409`, and `422` responses as expected contract behavior or actual failures.
6. Detect Scheduler jobs, backups, restore procedures, and scripts that depend on `PAY_*` or `pf-payroll`.

### Phase 2 — Decisions and approvals

Approve, document, and assign owners for:

1. Rotation or revocation of the exposed Scheduler credential.
2. Least-privilege IAM matrix.
3. `pf-rates` access to `PF_DATABASE_URL`.
4. Runtime/deployer identity separation.
5. Final Cloud Run CPU, memory, concurrency, region, and scaling configuration.
6. Schema maintenance window.
7. Rollback window of at least 14 calendar days and one representative payroll flow, whichever is longer.
8. Acceptance and rollback criteria, including baseline error classification.

### Phase 3 — Controlled changes, with explicit authorization

1. Rotate or revoke the exposed credential.
2. Correct Postman and known consumer configurations.
3. Apply the approved IAM changes.
4. Create target Artifact Registry, service account, and Secret Manager resources.
5. Apply and validate the reversible `pf-db` migration, including `INC_MV_PAY_SUMMARY` and its renamed columns.
6. Deploy `pf-svc-income` with immutable image identity and approved Cloud Run settings.
7. Execute health, authenticated, database, and representative smoke tests.
8. Start and monitor the approved rollback observation window.

The exposed credential is the urgent real security action. Phase 1 items are investigation/validation, Phase 2 items are decisions, and Phase 3 items are controlled mutations. No target resource, IAM change, database migration, consumer cutover, or deployment is authorized merely because it appears in this plan.

## 1.3 Phase 2 decision package

The following decisions are prepared from the Phase 1 evidence. They are proposals for explicit approval, not executed changes. No credential rotation, IAM mutation, resource creation, schema migration, consumer update, or deployment is authorized by this section alone.

### Decision 1 — Exposed Scheduler credential (out-of-band incident)

**Proposal:** treat the `X-API-Key` exposed in inventory 01 as an independent security incident, outside the scope and schedule of this rename. It belongs to `gcp-scheduler-runner`, not to `pf-payroll`, and must not wait for the Phase 2/Phase 3 sequence.

Incident actions, subject to explicit operational approval:

1. Rotate or revoke the key and update the enabled Scheduler job in the same change; validate the next scheduled execution.
2. Review `gcp-scheduler-runner` logs for unexpected use during the exposure window.
3. Remove or redact inventory 01 outputs, archives, and Cloud Shell history containing the literal value.
4. Evaluate replacing the static header with Scheduler OIDC authentication to Cloud Run as a follow-up; this is not required to unblock the rename if handled as a separate incident.

This plan tracks incident closure as a precondition for Phase 3, but the incident must be executed independently.

Status: pending explicit operational approval.

### Decision 2 — Runtime IAM

**Proposal for the new `pf-svc-income` runtime identity:**

- Secret Manager accessor only on `PF_DATABASE_URL`, `PF_RATES_API_KEY`, and the new `PF_INCOME_API_KEY`.
- No Artifact Registry writer permission.
- No Cloud Run Admin permission.
- No project-wide Service Account User permission.
- Cloud SQL Client only if later evidence proves the database is Cloud SQL; Phase 1 found no Cloud SQL instance in the project, so omit it initially.
- Call `pf-rates` over HTTPS using `PF_RATES_API_KEY`; do not grant `run.invoker` unless read-only policy evidence proves that IAM invocation is required.
- Define the new service's own invoker policy separately; the current exposure model is believed to be `allUsers` plus application API-key authentication, but this remains pending because the Cloud Run IAM query is blocked by VPC Service Controls.

Status: pending IAM review and approval.

### Decision 3 — `pf-rates` secret access

**Proposal:** retain `pf-rates` access to `PF_RATES_API_KEY`; determine read-only whether the current `pf-rates` revision mounts `PF_DATABASE_URL`. If it does not, remove its accessor binding as a separate change before or after the rename, never inside the maintenance window. If it does, document the dependency and retain the binding.

Follow-up, out of scope: both services appear to share one database credential. Per-service PostgreSQL roles would be the database-level least-privilege improvement.

Status: pending dependency confirmation and approval.

### Decision 4 — Runtime/deployer/administration identities

**Proposal:** use three separate identities:

- runtime: only the permissions in Decision 2;
- deployer: `roles/artifactregistry.writer` on the target repository, `roles/run.developer` on the target service, and `iam.serviceAccountUser` only on the runtime identity;
- setup/administration: temporary or administrator-operated resource and IAM setup, never reused as runtime.

The workflow currently documents a long-lived JSON `GCP_SA_KEY`, so WIF should be the preferred deploy-authentication target. The WIF condition should use immutable GitHub repository identity rather than repository name, so the rename does not break it. If WIF is deferred, use a dedicated deployer identity; do not reuse the runtime account's key.

Pending read-only evidence: deploy workflow authentication details and the account/key relationship must be reviewed without reading key material.

Status: pending IAM matrix approval.

### Decision 5 — Cloud Run configuration

**Proposal:** preserve the cost-conscious baseline provisionally, but do not freeze sizing before classifying the existing failures:

- region: `us-central1`;
- CPU: `1`;
- memory: `512Mi`, provisional until memory-limit evidence is reviewed;
- concurrency: `80`, provisional until the database connection budget is confirmed;
- timeout: `300s`, provisional until timeout evidence is reviewed;
- `min-instances=0`;
- immutable image digest/tag for each deployment;
- replace the TCP startup probe with HTTP `GET /health` on port `8080` only if `/health` is confirmed independent of the database and `pf-rates`.

The service currently exposes `maxScale=20` and the revision evidence previously showed `maxScale=2`. Resolve this to one explicit value at both service and revision level. Size the cap from the database connection budget, including both old/new services during blue/green, `pf-rates`, and migration headroom; `2` is a provisional default, not an approved final value.

Status: pending error classification, connection-budget evidence, and operational approval.

### Decision 6 — Schema maintenance window

**Proposal:** use a short maintenance boundary rather than a zero-downtime physical rename.

Before the window:

- backup and restore readiness from Decision 9 is confirmed;
- the deterministic parity suite from Decision 8 is run against `pf-payroll`;
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

Status: pending backup/restore readiness and operational approval.

### Decision 7 — Rollback window

**Proposal:** retain rollback capability for **14 calendar days and until one representative monthly payroll flow completes**, whichever is longer. Define the flow as import, review/summary, and export using synthetic or explicitly approved operational data.

During the window:

- retain the old service, image, account, and `PF_PAYROLL_API_KEY`;
- retain the old account's access to the old secret until closure;
- keep the old service at scale-to-zero and isolated from normal traffic;
- freeze `pf-db` migrations touching `INC_*` objects and dependencies under Decision 11.

Rollback is not merely redirecting traffic: stop the new service, run the tested schema downgrade, verify the original `PAY_*` schema and `PAY_MV_SUMARY` columns, restore old-service access, and run the parity suite. New migrations during the window invalidate this downgrade path unless explicitly re-approved.

Status: pending approval of observation period, flow definition, and freeze policy.

### Decision 8 — Error classification and acceptance criteria

#### 8a. Baseline classification

Classify each `500`/`502` by authorized logs or application diagnostics as contract behavior, client error, application defect, downstream `pf-rates` failure, memory limit, or timeout. Record a disposition: fix before cutover, accept as known issue, or not reproducible. Confirm `400`, `403`, `404`, `409`, and `422` with deterministic synthetic cases, and correct the single plain-HTTP `302` consumer to HTTPS.

This classification is an input to Decision 5; the current request volume is too small for a rate-based regression threshold.

#### 8b. Contract parity suite

Run deterministic synthetic tests against `pf-payroll` before cutover and record expected status codes and response shapes for:

- `/health`;
- authentication failure;
- reads;
- JSON and spreadsheet import;
- PDF preview;
- export;
- reference data;
- deliberate `404`, `409`, and `422` cases.

Run the same suite against `pf-svc-income`, allowing only approved contract renames such as `period_id → payroll_id`, payroll-oriented DTOs, and `accrual_year`/`accrual_month`.

#### 8c. Go/No-Go and closure

Go/No-Go requires the parity suite, catalog assertions, equal row counts, equal materialized-view aggregates, approved `INC_*` columns, and at least one successful `pf-rates` call. Rollback-window closure additionally requires all known consumers migrated, the representative flow completed, every new `5xx` explained/dispositioned, and a tested downgrade on representative synthetic data. “Rollback tested or excluded” is not sufficient for this plan.

Status: pending classification and approval.

### Decision 9 — Neon backup and restore

**Proposal:** use Neon as the PostgreSQL provider behind `PF_DATABASE_URL`. Confirm the Neon project/branch and backup/PITR retention through the Neon console or approved Neon API without exposing the connection string. Independently take a restricted logical dump immediately before migration, restore it into an ephemeral PostgreSQL 16 instance, verify Alembic/schema state and row counts, and delete it after rollback closure. The dump contains sensitive payroll data and must never enter git or shareable inventory artifacts.

The existing `pf-db` tooling reads `NEON_DATABASE_URL` from the gitignored `secrets/neon.env`; never copy that value into commands, logs, proposals, or chat. Because a production dump contains RUTs, salaries, health information, and other sensitive data, its storage, encryption, access, retention, and deletion must be explicitly approved.

**Security finding:** the operator's response artifact contained a complete `NEON_DATABASE_URL` line from the gitignored local environment. The local response copy was redacted, but the Neon credential must be treated as exposed and rotated in the Neon control plane; deleting the response file alone is insufficient. The 2.1 MB logical dump is also sensitive payroll data and must remain restricted outside git/shareable inventory paths or be deleted according to the approved retention decision.

**Control-plane evidence received:** Neon project `personal-finances` (`wild-hat-66882594`) is in `aws-us-east-1`, with default/primary branch `production` (`br-empty-glade-adv4sfa9`) in `ready` state. Its read/write endpoint is in `aws-us-east-1`, currently `idle`, with autoscaling `0.25–2` CU and suspend timeout `0`. Project history retention is only **6 hours**, so it cannot by itself cover the proposed 14-day rollback window. The approved logical dump must therefore be stored securely for the rollback window, with a tested restore and explicit deletion decision.

**Role evidence received:** the application connection uses `neondb_owner`. This role is not a least-privilege runtime role: the inspected role metadata shows `rolcreaterole`, `rolcreatedb`, and `rolbypassrls` enabled. **Role evidence update:** the corrected grant query shows `neondb_owner` has all listed DML/DDL-related table privileges on every `PAY_*`/`RAT_*` object, all grantable. All objects are owned by `neondb_owner`. Role membership shows `neondb_owner` inherits `neon_superuser`; `neon_service` also inherits `neon_superuser`, which has broad `pg_read_all_data` and `pg_write_all_data` memberships. These roles are unsuitable as least-privilege application identities. A dedicated runtime role design is mandatory before deployment; do not improvise grants during the schema migration.

**Privilege evidence update:** `neondb_owner` has database `CREATE`/`TEMPORARY`, public schema `CREATE`, and all grantable table privileges. All application objects and sequences are owned by `neondb_owner`. Default privileges from `cloud_admin` grant broad table/sequence access to `neon_superuser`. No row-level security policies were returned; the first RLS flag query was version-incompatible and requires the corrected catalog query. Public routines returned no security-definer application routine; most results were extension support functions.

**Snapshot schedule evidence:** `neonctl snapshots schedule get` confirms that no automatic snapshot schedule is configured for the `production` branch. Since History Retention is only 6 hours, the approved logical dump and tested restore remain the required rollback evidence for the proposed 14-day window.

The operator also created a 2.1 MB logical dump and restored it into the local PostgreSQL environment successfully. The dump contains real payroll data and must remain restricted, outside git/shareable inventory locations, until its approved deletion point. This is evidence of local restore tooling, not yet a production backup-retention or restore-rehearsal approval.

The materialized-view column query returned zero rows because the chosen `information_schema.columns` query did not expose the materialized-view columns; use `pg_attribute`/catalog inspection before treating the result as an empty view. The local `migration-check`/`alembic current` command was initially inconclusive because it was run from the wrong path and the host environment lacked the required driver; the later `uv run alembic current` reached Neon and confirmed `0016 (head)`.

**Updated proposal:** retain the Neon provider/backup decision, confirm Neon retention/PITR through the approved Neon control plane, inspect the materialized-view columns with a catalog query, and rehearse restore validation locally with the correct `pf-db` environment before approving the migration window.

Status: Neon connection and local restore evidence received; provider retention/PITR approval and final restore rehearsal remain pending.

### Decision 10 — GitHub deploy authentication

**Proposal:** replace the documented long-lived JSON `GCP_SA_KEY` workflow authentication with Workload Identity Federation. Bind using immutable GitHub repository identity rather than repository name so the rename survives. Coordinate the change through `pf-common` without breaking `pf-rates`. If WIF is deferred, use a dedicated deployer identity and explicit rotation date; never reuse the runtime identity's key.

The current workflow documentation proves the JSON-key pattern, but not which account's key is stored in the secret. That relationship requires read-only operational evidence without reading key material. Review `GH_PAT` scope separately.

Status: pending approval with Decision 4.

### Decision 11 — `pf-db` change freeze

**Proposal:** from cutover until rollback closure, do not merge migrations touching `INC_*` objects or dependencies. An emergency exception must explicitly restate or invalidate the downgrade path.

Status: pending approval.

### Decision 12 — Invocation, ingress, and URLs

**Proposal:** preserve the current exposure model only after it is verified: Cloud Run currently advertises `ingress=all` and both URL formats, while the service IAM policy remains blocked by VPC Service Controls. Verify whether `allUsers` invoker plus application API-key authentication is the intended model for the new service. Verify `pf-rates` invoker policy separately.

The current service advertises both:

```text
https://pf-payroll-646185261155.us-central1.run.app
https://pf-payroll-yqd4p7fzaa-uc.a.run.app
```

Therefore the deterministic URL is not automatically “stale”. Prepare the target Postman URL only after the new service exists or after the URL behavior is confirmed for the target service; do not assume a URL before creation.

Status: pending IAM/URL evidence and approval.

### Decision 13 — Neon runtime roles

**Proposal:** do not use Neon owner-level `neondb_owner` as the runtime identity for `pf-svc-income`. Create or select a dedicated application role with only the privileges required by the service, and separate roles for `pf-rates` and the renamed service if the provider and migration strategy permit it. Map exact table, sequence, schema, and materialized-view privileges before migration; do not grant `CREATEROLE`, `CREATEDB`, `BYPASSRLS`, or owner privileges to runtime roles.

The corrected response includes `table_name` and confirms the broad privilege pattern; the exact least-privilege target matrix still needs to be designed from application DML. This decision is database-level least privilege and must be coordinated with `pf-db`; it must not be improvised inside the production migration window.

Status: exact privilege evidence received; dedicated role design and approval remain pending.

### Phase 2 gate

Resolution order:

1. Handle Decision 1 independently as a security incident.
2. Obtain read-only evidence for Decisions 2–5, 9, 10, and 12; complete Decision 8a.
3. Approve Decisions 2, 3, 4, 5, 10, and 12.
4. Approve Decisions 6, 7, 9, 11, and 13.
5. Approve Decision 8b–8c.

Phase 3 remains blocked until Decision 1 is closed and Decisions 2–13 are approved with named approver, date, and evidence link. Preparing this package does not rotate credentials, change IAM, create GCP resources, alter the database, update Postman, or deploy a service.

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
