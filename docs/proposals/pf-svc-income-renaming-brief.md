# Brief: rename `pf-payroll` to `pf-svc-income`

Date: 2026-10-05
Status: approved
Level: L
Scope: Cross-repository/service identity rename from `pf-payroll` to `pf-svc-income`.
Related artifacts: [investigation](../investigations/pf-svc-income-renaming.md) · [recommendation](pf-svc-income-renaming-recommendation.md) · [plan](pf-svc-income-renaming-plan.md)
Approved: 2026-10-05 by a0a11b7 (conversation)
Superseded-by: none

> Investigation update 2026-10-07: GCP inventory 01–03 is now available. The current service and rollback baseline are documented; no `pf-svc-income` GCP resource or secret has been created. Implementation remains blocked pending security remediation, IAM design, error classification, and explicit operational authorization.

## Objective

Define and safely execute the rename of the current payroll service and repository to `pf-svc-income`, including a bounded package split: payroll-specific flows remain in `payroll`, while genuinely shared income capabilities move to a second package consumed by payroll and future independent bonuses/honorarios. The work also includes renaming the current `PAY_*` database objects to an `INC_*` vocabulary, with `PAY_PERIOD → INC_PAYROLL`.

The renamed service must be able to grow beyond monthly employment payroll toward independently paid employment bonuses and Chilean honorarios without making the current payroll model or API contract invalid.

## Non-objectives

- Implementing honorarios processing.
- Implementing off-cycle bonus calculations.
- Redesigning the `/payroll/*` route family is not included; those routes remain payroll-specific. The detail route parameter becomes `/payroll/{payroll_id}` as a semantic/OpenAPI rename, while the concrete URL remains `/payroll/481`.
- The shared package must not import payroll infrastructure or repositories.
- Renaming the shared PostgreSQL database, `PF_DATABASE_URL`, `pf-rates`, `pf-db`, or `pf-common`.
- Creating or deleting production resources without explicit authorization.
- Committing, pushing, approving deployment gates, or rotating secrets autonomously.

## Affected repositories and boundaries

| Repository | Responsibility in this change |
|---|---|
| `pf-base` | Coordination artifacts, root indexes, architecture, Postman collection/environments, ecosystem README/AGENTS references |
| `pf-payroll` → `pf-svc-income` | Application, package/build identity, workflows, service docs, tests, deployment configuration |
| `pf-db` | Physical schema migration owner: rename `PAY_*` objects to `INC_*`, update dependent DDL/docs/scripts, validate upgrade/downgrade |
| `pf-common` | Reusable workflow examples and path/dependency-sync references |
| `pf-rates` | Consumer/deployment coordination check only; no expected code change |
| `pf-sheets` | Consumer inventory check only; no expected code change unless evidence identifies a dependency |

## Latest investigation findings

The 2026-10-07 inventory confirms:

- `pf-payroll` remains the only deployed payroll service, at 100% traffic, with ready revision `pf-payroll-00056-lgk`;
- the active image is pinned by digest in Artifact Registry repository `pf-payroll`;
- Artifact Registry `pf-svc-income`, service account `pf-svc-income@coreassistant-474022.iam.gserviceaccount.com`, and secret `PF_INCOME_API_KEY` do not exist;
- current runtime uses `PF_DATABASE_URL`, `PF_PAYROLL_API_KEY`, `PF_RATES_API_KEY`, and `PF_RATES_URL`;
- observed consumers include Postman and curl, using both historical and current Cloud Run URLs;
- no VPC connector, custom domain, forwarding rule, DNS zone, alert policy, dashboard, log-based metric, or uptime check was found in the collected artifacts;
- an API key was exposed in the earlier Scheduler inventory and must be rotated before sharing or closing the investigation;
- the existing runtime service account has broad project roles and must not be copied blindly to the target runtime account.

The new service, secret, account, and repository remain design/deployment work, not completed outcomes.

## Research validation update (2026-10-07)

- Local route inspection found 27 application routes; `docs/api.md` covers all of them plus FastAPI's `/docs`, `/redoc`, and `/openapi.json`.
- Postman has 28 payroll requests and covers the functional operations, but examples use literal IDs in places where the target contract will rename `period_id` to `payroll_id`.
- A local PostgreSQL 16 synthetic transaction renamed the full `PAY_*` map to `INC_*`, preserved synthetic rows/IDs and FKs, and rolled back cleanly to Alembic revision `0016`.
- GitHub workflows, environments, recent successful runs, and the unprotected `main` branch were confirmed read-only. GitHub secret-list endpoints returned HTTP 500, so secret preservation remains unverified rather than absent.
- Proposed rollback window: 14 calendar days and at least one representative payroll flow, whichever is longer.

## Constraints and invariants

- `pf-db` remains the DDL/migration owner and must ship the physical object rename before service traffic uses the new ORM names.
- The shared package must not import payroll infrastructure or repositories.
- Existing `INC_*` tables, keys, FKs, materialized views, and current payroll API behavior remain compatible after the approved migration.
- `/payroll/*` remains the current public route namespace during this rename.
- `PF_DATABASE_URL`, `PF_RATES_URL`, and `PF_RATES_API_KEY` remain unchanged.
- No real payslips, RUTs, salaries, health information, credentials, or production secret values enter git.
- Deployment keeps scale-to-zero and the existing free Trivy security scan; no new paid infrastructure is introduced.
- Repository-specific `AGENTS.md` rules, quality gates, and explicit release authorization remain mandatory.

## Required observable outcomes

1. The GitHub repository, local module folder, Python distribution, deployment configuration, and current documentation consistently identify the service as `pf-svc-income`; `payroll` remains as a payroll package and `income` is the extracted shared package.
2. A clean checkout of the renamed repository installs, imports, starts, and passes the full service quality gate.
3. CI builds an image using the new service identity and targets the new Cloud Run/Artifact Registry resources without changing the shared database.
4. The new deployment can reach `pf-rates`, `pf-db`, and required secrets using least-privilege IAM.
5. The existing `/payroll/*` route family remains available after cutover; the detail route metadata uses `payroll_id`, and intentional payroll DTO/field renames are documented and synchronized with clients.
6. Postman, OpenAPI, service docs, root docs, architecture diagrams, and deployment runbooks agree on the new service identity and current endpoint paths.
7. Existing `INC_*` tables remain readable/writable by the renamed service after the reversible migration, with no row/ID/FK loss.
8. The old deployment remains available for a documented rollback window, or an explicit decision records why no window is required.
9. All external consumers and live GCP resources are inventoried before cutover.

## Compatibility requirements

- Preserve old repository URL redirects long enough to update consumers.
- Decide whether old and new Cloud Run URLs are both served during migration.
- Cut over API authentication atomically to `PF_INCOME_API_KEY`; do not add application fallback to `PF_PAYROLL_API_KEY`.
- Preserve `/payroll` routes and use `/payroll/{payroll_id}`; future `/income` or `/honorarios` APIs are separate scope.
- Rename current physical `PAY_*` objects to the approved `INC_*` map; do not turn them into a generic income ledger.

## Testing expectations

At minimum:

- repository-wide reference scan with no unintended old identity references;
- package import and Uvicorn application startup checks;
- unit/integration tests using synthetic data;
- Docker build and container health check;
- generated OpenAPI comparison against `docs/api.md`;
- Postman collection/environment variable validation;
- `pf-db` migration validation with upgrade/downgrade and synthetic existing data, proving the approved `PAY_*` → `INC_*` rename and no row/ID/FK loss;
- DTO/route contract tests proving `period_id → payroll_id`, period-oriented DTO names become payroll-oriented, intentional collection-field changes are documented, and concrete `/payroll/{value}` URLs remain unchanged;
- CI quality gates for `pf-svc-income`, `pf-common`, and affected root documentation;
- pre-cutover live checks for Cloud Run, Artifact Registry, IAM, secrets, database, and `pf-rates` connectivity;
- post-cutover `/health` and representative protected endpoint smoke tests;
- rollback smoke test against the old service if it remains during the window.

## Cost and operational questions

- Can the rename use one parallel Cloud Run service only during cutover, with `min-instances=0`, instead of adding a permanent service?
- Can the old Artifact Registry repository and old Cloud Run service be retained only for a bounded rollback window?
- Does creating a new service account incur no direct cost but add IAM/rotation/support burden?
- Are there existing custom domains, monitoring, log-based metrics, or scheduled jobs that change the operational scope?

## Decisions required before implementation

- Package split: retain `payroll`; introduce shared `income` capabilities and dependency tests.
- Proposed ecosystem database identifier limit is 30 characters; PostgreSQL's 63-byte limit remains the technical ceiling.
- `PAY_MV_SUMARY` is explicitly renamed to `INC_MV_PAY_SUMMARY`, correcting the historical spelling and making the payroll domain visible in the materialized-view name.
- Explicit index and constraint naming policy: names must remain below PostgreSQL's 63-byte identifier limit, be unique within the schema, and must not depend on automatic truncation.
- HTTP route policy (`/payroll/*` retained or separate API migration).
- API key migration strategy.
- Old URL rollback/compatibility window and objective exit criteria.
- Approval of the recommendation and implementation plan.

## Requested recommendation format

The recommendation must state the chosen rename depth, compatibility strategy, GCP migration sequence, database policy, cross-repository order, validation matrix, rollback procedure, cost impact, and remaining approvals. It must reject any unnecessary destructive table rename explicitly.
