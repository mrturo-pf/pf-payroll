# Investigation: rename `pf-payroll` to `pf-svc-income`

Date: 2026-10-05
Status: investigating
Level: L
Scope: Ecosystem-wide identity rename of the `pf-payroll` service/repository to `pf-svc-income`, including repository, source package, database ownership references, GCP resources, CI/CD, secrets, documentation, and compatibility.
Related artifacts: [brief](../proposals/pf-svc-income-renaming-brief.md) · [recommendation](../proposals/pf-svc-income-renaming-recommendation.md) · [plan](../proposals/pf-svc-income-renaming-plan.md)
Approved: pending user decision
Superseded-by: none

## 1. Investigation question

What must change, and what must deliberately remain stable, if the independent `pf-payroll` project is renamed to `pf-svc-income`?

The requested target name is `pf-svc-income`. The user has now decided that the GitHub repository target is exactly `mrturo-pf/pf-svc-income`, that the target API key is `PF_INCOME_API_KEY`, that the source will retain a payroll package while extracting genuinely shared income capabilities into a second package, and that existing `PAY_*` tables will be renamed to an `INC_*` vocabulary, with `PAY_PERIOD` becoming `INC_PAYROLL`. This investigation treats the work as a Level L change because it crosses repository, source-package, schema, deployment, secret, public-contract, and operational boundaries. No rename, migration, push, deployment, secret rotation, or production mutation was performed during this investigation.

## 2. Repositories and source-of-truth files inspected

Inspected with clean working trees on `main`:

- root repository `pf-base` at `/Users/a0a11b7/Documents/reps-personal/pf`;
- service repository `pf-payroll`, remote `https://github.com/mrturo-pf/pf-payroll`;
- schema repository `pf-db`, remote `https://github.com/mrturo-pf/pf-db`;
- shared tooling repository `pf-common`;
- root Postman collection and environments;
- root architecture source and generated documentation references.

Primary governing documents:

- root `AGENTS.md` and `README.md`;
- `modules/pf-payroll/AGENTS.md` and `README.md`;
- `docs/ecosystem-improvement-workflow.md`;
- `modules/pf-db/AGENTS.md`, `README.md`, `docs/tables.md`, `docs/ci.md`;
- `modules/pf-payroll/docs/deployment.md`, `docs/database.md`, `docs/api.md`, and `docs/getting-started.md`;
- `modules/pf-payroll/.github/workflows/deploy.yml` and `gcp-setup.yml`;
- `modules/pf-common/.github/workflows/deploy-reusable.yml` and `gcp-setup-reusable.yml`.

The root `AGENTS.md` is over the configured 10,000-character read limit and was reported as truncated by the environment. Its visible rules were followed; the file should be trimmed or the cap increased before implementation.

## 3. Current identity map

| Surface | Current value | Evidence | Impact |
|---|---|---|---|
| Local module | `modules/pf-payroll` | root README, filesystem | Rename directory and every relative consumer |
| GitHub repository | `mrturo-pf/pf-payroll` | git remote | Repository rename and external links |
| Python distribution | `pf-payroll` | `pyproject.toml` | Distribution metadata, build/tests |
| Python package | `payroll` | `src/payroll/`, imports, Docker CMD | Retain for payroll-specific flows; introduce a second `income` package for shared capabilities |
| Domain/API vocabulary | `payroll` | routes, use cases, DTOs, docs | Not all occurrences should change: `/payroll` is a public compatibility contract |
| API key env/secret | `PF_PAYROLL_API_KEY` → `PF_INCOME_API_KEY` | config, `.env.example`, workflows, Postman docs | Atomic secret cutover; no application fallback |
| Deployment workflow input | `repo_name: pf-payroll` | `.github/workflows/deploy.yml` | Controls checkout, image, registry, Cloud Run, concurrency |
| GCP Artifact Registry | `pf-payroll` | workflow and deployment docs | New repository or retained resource decision |
| GCP Cloud Run service | `pf-payroll` | deployment docs/workflow conventions | New service is required for a safe blue/green cutover; old service cannot be renamed in place |
| GCP runtime service account | `pf-payroll@PROJECT...` | deployment docs/workflow conventions | IAM principal migration/grant and later cleanup |
| Secret Manager service key | `PF_PAYROLL_API_KEY` → `PF_INCOME_API_KEY` | workflow/docs/Postman | New runtime accepts only the new name; old secret retained only for operational rollback |
| Database connection | `PF_DATABASE_URL` | service/db docs | No rename required; shared database identity is generic |
| DB ownership label | payroll / `pf-payroll` | `pf-db` AGENTS/docs and migration comments | Documentation and ownership update |
| Physical database tables | `PAY_*` and `PAY_MV_SUMARY` | `pf-db` migration `0003`, schema/docs, ORM | Rename through a new `pf-db` migration to the approved `INC_*` vocabulary; `PAY_PERIOD` becomes `INC_PAYROLL` |
| Root module index | `pf-payroll` | root README/AGENTS | Update links and topology |
| Architecture diagrams | `pf-payroll`, `pf_payroll`, `pf-payroll.html` | `architecture/*.json`, generated HTML/index | Regenerate after source update |
| Postman variables | `pf-payroll-url`, `pf-payroll-api-key` → `pf-svc-income-url`, `pf-svc-income-api-key` | environment/collection/README | Atomic client cutover; endpoint paths remain payroll-specific |
| Current deployed URL | `https://pf-payroll-646185261155.us-central1.run.app` | root GCP Postman environment | New URL must be discovered after deployment; old URL needs compatibility window |

## 4. Current behavior and dependency facts

The service is currently a Chilean payroll calculation and import service. Its domain is not yet a generic income ledger:

- `PAY_PERIOD` is monthly and unique by employer/year/month.
- Employment contracts, AFP, health, unemployment insurance, tax, payslip PDF import, and payroll line items are first-class concepts.
- The API is publicly documented under `/payroll/*`.
- `pf-rates` is consumed over HTTP; no database rename is needed for that dependency.
- `pf-sheets` calls `pf-rates`, not `pf-payroll`; no direct Apps Script runtime dependency on the payroll URL was found in the inspected source.
- `pf-common` contains path-based dependency synchronization and documentation that explicitly names `pf-payroll`.
- `pf-db` owns DDL and migrations; the service owns ORM models/repositories. Any physical table change belongs to `pf-db` first.

The target name can be interpreted as a service identity umbrella for payroll, off-cycle employment payments, and future honorarios. It does not by itself change the existing domain model or public API.

## 5. Reference inventory

### 5.1 Repository and local paths

Required updates:

- GitHub remote and repository links.
- `modules/pf-payroll` directory name.
- Relative links from root, `pf-db`, `pf-common`, architecture, and service docs.
- CI checkout paths and working directories.
- Any local scripts, Make targets, compose context, or developer instructions using `../pf-payroll`.

The repository rename should be performed through GitHub's supported rename operation, not by creating a second unrelated repository. GitHub redirects are useful but must not be treated as a permanent migration strategy.

### 5.2 Python/build identity and package split

The distribution and service identity become `pf-svc-income`, but the internal source split is deliberately not a wholesale `payroll` → `income` replacement.

Retain a `payroll` package for concepts that require employment/payroll semantics:

- payroll import and reconciliation;
- `PayrollPeriod`, payroll summaries, items, and payroll queries;
- employer and employment-contract maintenance;
- AFP, health, unemployment insurance, complementary insurance;
- monthly employment income-tax calculation;
- payslip PDF extraction/templates;
- payroll spreadsheet export;
- payroll-specific repositories and DTOs.

Introduce a second package, recommended as `income`, only for stable shared capabilities that future off-cycle bonuses and honorarios can consume:

- money and currency value objects;
- CLP/currency/percentage quantization;
- generic nominal-to-real deflation calculations;
- market-data ports and exchange-rate/index resolution;
- generic income-event primitives only after their invariants are defined.

Current code-grounded extraction candidates:

| Current location | Initial disposition | Reason |
|---|---|---|
| `domain/quantizers.py` | Move or re-home under `income.domain` | No payroll-specific invariant in the helpers |
| `domain/deflation.py` | Move or re-home under `income.domain` | Algorithm is generic, though the current use case is not |
| `application/services/exchange_rates.py` | Move or re-home under `income.application` | Resolves market data and has no payroll table dependency |
| `MarketDataRepository` protocol | Split into an income market-data port, with payroll adapter compatibility | Future income flows will need the same provider boundary |
| `MoneyDTO`, currency/rate/index DTOs | Extract only after reviewing all field semantics | Some are already generic; payroll DTOs must not be dragged along |
| `DeflateAmounts` use case | Keep as a payroll adapter initially; later generalize behind an income read port | It currently depends on `PayrollRepository` and payroll summaries |
| `ComputeIncomeTax` | Keep in `payroll` initially | Current implementation is Chilean monthly employment withholding tied to payroll persistence; honorarios rules are not proven equivalent |

This split must not become a dumping ground. Do not move a use case merely because its name contains `income`, `amount`, or `tax`. The common package should expose ports and small domain services; payroll should compose them for payroll workflows, and future bonus/honorarios modules should compose them independently.

Required updates:

- add the second package under `src/income`;
- update imports only for extracted modules;
- retain `src/payroll` and its public payroll route wiring;
- update package discovery, Docker packaging, tests, diagrams, and documentation;
- add architecture tests preventing `income` from importing payroll infrastructure or payroll repositories;
- define dependency direction `payroll → income`, never `income → payroll`.

The HTTP route question is separate: the previous question was about `/payroll/*` paths, not the filesystem package directory. The package split does not decide it. The compatibility default remains to preserve `/payroll/*` until a separate API migration is approved.

### 5.3 CI/CD

`pf-payroll/.github/workflows/deploy.yml` passes `repo_name: pf-payroll` to `pf-common`'s reusable workflow. That input controls:

- checkout directory;
- Docker build context;
- image name;
- Artifact Registry repository;
- Cloud Run service name;
- runtime service account;
- concurrency group `deploy-<service>-production`;
- deployment smoke-test target.

The one-time GCP setup workflow passes `service_name: pf-payroll`, which controls Artifact Registry, API-key secret creation, and IAM grants. Both workflows must be updated together with the reusable workflow's allowed-name documentation and any comments/examples.

### 5.4 GCP resources

Likely current logical names:

- Artifact Registry repository `pf-payroll`;
- Cloud Run service `pf-payroll`;
- runtime/deployer service account `pf-payroll@PROJECT_ID.iam.gserviceaccount.com`;
- Secret Manager `PF_PAYROLL_API_KEY`;
- shared `PF_DATABASE_URL` and `PF_RATES_API_KEY`.

A Cloud Run service name is not an in-place rename. A safe migration requires deploying `pf-svc-income` beside `pf-payroll`, validating it, moving consumers/traffic, and retaining the old service during a rollback window. Artifact Registry repositories are also separate resources; image history is not automatically moved by a repository rename. Service-account renaming is not an in-place operation; a new principal requires IAM grants and secret access.

No live GCP mutation or `gcloud` inventory was performed in this investigation. The exact project, resources, revisions, IAM bindings, secret versions, DNS/custom domains, and monitoring alerts remain a live-environment verification gate.

### 5.5 Secrets and environment variables

Candidate new service identity:

- `PF_INCOME_API_KEY` instead of `PF_PAYROLL_API_KEY`.

`PF_DATABASE_URL`, `PF_RATES_URL`, and `PF_RATES_API_KEY` are not service-name-specific and should remain unchanged. The API key cutover is atomic: the new runtime accepts only `PF_INCOME_API_KEY`; it does not fallback to `PF_PAYROLL_API_KEY`. The old secret can remain stored solely for operational rollback until the rollback window closes.

### 5.6 Database and schema

The user has decided to rename the physical payroll-owned tables into an `INC_*` vocabulary, with the central period table becoming `INC_PAYROLL`. This is a real schema migration owned by `pf-db`, not a documentation-only rename.

The approved target map uses the proposed 30-character ecosystem limit. PostgreSQL still permits up to 63 bytes per identifier; the longest target, `INC_PAY_PDF_TEMPLATE_FIELD`, is 26 ASCII characters.

| Current | Target |
|---|---|
| `PAY_PENS_INST` | `INC_PENS_INST` |
| `PAY_HLTH_INST` | `INC_HLTH_INST` |
| `PAY_PENS_PLAN` | `INC_PENS_PLAN` |
| `PAY_HLTH_PLAN` | `INC_HLTH_PLAN` |
| `PAY_CNTRB_CAP` | `INC_CNTRB_CAP` |
| `PAY_COMP_PROV` | `INC_COMP_PROV` |
| `PAY_COMP_PLAN` | `INC_COMP_PLAN` |
| `PAY_EMPLOYER` | `INC_EMPLOYER` |
| `PAY_EMP_CONT` | `INC_EMP_CONT` |
| `PAY_PERIOD` | `INC_PAYROLL` |
| `PAY_PRD_HLTH` | `INC_PAY_PRD_HLTH` |
| `PAY_PRD_COMP` | `INC_PAY_PRD_COMP` |
| `PAY_CONCEPT` | `INC_PAY_CONCEPT` |
| `PAY_ITEM` | `INC_PAY_ITEM` |
| `PAY_PDF_TEMPLATE` | `INC_PAY_PDF_TEMPLATE` |
| `PAY_PDF_TEMPLATE_FIELD` | `INC_PAY_PDF_TEMPLATE_FIELD` |
| `PAY_MV_SUMARY` | `INC_MV_PAY_SUMMARY` |

The mapping must respect the 30-character convention, foreign-key dependency order, and the fact that `INC_PAYROLL` is a payroll-period table rather than a generic income ledger. `INC_MV_PAY_SUMMARY` explicitly corrects the historical `SUMARY` spelling and makes the payroll domain visible.

The migration must update, in dependency order:

- tables and all foreign keys;
- indexes, unique constraints, check constraints, and materialized-view SQL;
- seed SQL and restore/inspection scripts;
- ORM `__tablename__` values and repository SQL;
- tests, fixtures, Postman descriptions, and database docs;
- migration comments and ownership documentation.

The migration must be reversible and tested against a copy of existing data. It must use table renames, not drop/recreate operations for base tables, preserve IDs and rows, and verify every foreign key, index, view refresh, seed, rollback, and application query. The materialized view is the intentional exception: because its name and exposed column aliases change, the migration must recreate it from the approved definition after renaming the base tables/columns. `pf-db` remains the schema owner; `pf-svc-income` follows after the migration is available.

`PF_DATABASE_URL` and the PostgreSQL database name `pf_db` remain unchanged.

### 5.6.1 Database migration scope beyond table names

The approved migration is not only `PAY_* → INC_*`. It has three explicit layers.

#### Database identifier length policy

The proposed `INC_*` targets are validated against PostgreSQL and the ecosystem tooling. The ecosystem convention should increase the maximum table/materialized-view name length from **14 to 30 characters**. PostgreSQL's standard identifier limit remains **63 bytes**, so the proposed longest target, `INC_PAY_PDF_TEMPLATE_FIELD` (26 ASCII characters), remains safely valid.

The approved target vocabulary is the mapping recorded in section 5.6 above. The length policy applies to every target in that map, including `INC_MV_PAY_SUMMARY`.

The target `INC_MV_PAY_SUMMARY` intentionally makes the payroll domain explicit and corrects `SUMARY` to `SUMMARY`. It has 18 ASCII characters and remains within the proposed 30-character convention. This is an explicit rename decision, not an incidental spelling cleanup.

The migration must keep table and view names explicit and quoted. Index and constraint names must also be explicit, unique within the schema, and kept below PostgreSQL's 63-byte limit; do not rely on automatic truncation or generated-name collision handling. Update the documented convention and validators in `pf-db` before implementing the migration.

#### A. Preserve physical identity where possible

Do not change primary-key strategy or data types merely for the rename:

- keep every surrogate `id BIGSERIAL` primary key;
- preserve sequence values, ownership, and existing IDs;
- keep monetary `NUMERIC` precision/scale;
- keep enum types and their values unless a separate domain decision requires otherwise;
- keep nullable/default behavior and row contents;
- do not introduce a generic `income` table or add nullable honorarios columns to payroll tables.

A table rename must not silently change cardinality, nullability, defaults, or calculation semantics.

#### B. Rename relationship and temporal columns for domain language

The following column renames are included because they encode the old “period” vocabulary:

| Current object/column | Target object/column | Reason |
|---|---|---|
| `PAY_PERIOD.id` | `INC_PAYROLL.id` | Preserve PK column `id`; table identity changes |
| `PAY_PRD_HLTH.period_id` | `INC_PAY_PRD_HLTH.payroll_id` | FK now points to a payroll |
| `PAY_PRD_COMP.period_id` | `INC_PAY_PRD_COMP.payroll_id` | FK now points to a payroll |
| `PAY_ITEM.period_id` | `INC_PAY_ITEM.payroll_id` | FK now points to a payroll |
| `PAY_MV_SUMARY.period_id` | `INC_MV_PAY_SUMMARY.payroll_id` | Analytics output follows entity language |
| `PAY_PERIOD.period_year` | `INC_PAYROLL.accrual_year` | Current field identifies the worked/devengado month |
| `PAY_PERIOD.period_month` | `INC_PAYROLL.accrual_month` | Current field identifies the worked/devengado month |
| `PAY_MV_SUMARY.period_year` | `INC_MV_PAY_SUMMARY.accrual_year` | View mirrors the payroll accrual month |
| `PAY_MV_SUMARY.period_month` | `INC_MV_PAY_SUMMARY.accrual_month` | View mirrors the payroll accrual month |

The base table's PK remains `id`, not `payroll_id`, because changing every surrogate-key column would create unnecessary ORM, sequence, FK, and client churn. The semantic name belongs in DTOs, commands, route parameters, and FK columns; the base PK can remain the conventional `id`.

#### C. Recreate the materialized view with semantic column names

`INC_MV_PAY_SUMMARY` must change both its physical name and its exposed period-oriented columns. The target definition must expose:

| Current | Target |
|---|---|
| `PAY_MV_SUMARY` | `INC_MV_PAY_SUMMARY` |
| `period_id` | `payroll_id` |
| `period_year` | `accrual_year` |
| `period_month` | `accrual_month` |

The migration steps are:

1. stop old traffic and enter the approved schema maintenance boundary;
2. capture and verify the current materialized-view definition and unique-index metadata;
3. drop the old materialized view and its dependent unique index, or use an equivalent transactional replacement sequence;
4. rename the base tables and base columns in the approved dependency order;
5. create `INC_MV_PAY_SUMMARY` with the updated table references and column aliases `payroll_id`, `accrual_year`, and `accrual_month`;
6. recreate the unique index with an explicit, unique name below PostgreSQL's 63-byte identifier limit;
7. refresh the materialized view and verify its rows, columns, aggregates, and index;
8. update the SQLAlchemy model, repository refresh query, inspection SQL, architecture/schema documentation, and tests;
9. validate upgrade, downgrade, and rollback semantics on a fresh database and a copy containing representative synthetic data.

The view's calculated columns remain unchanged: `employer_id`, `payment_date`, `taxable_income_clp`, `gross_income_clp`, `total_discounts_clp`, and `net_pay_clp`. The migration must prove that the rename changes identifiers and aliases without changing aggregate semantics or losing rows. A downgrade must restore `PAY_MV_SUMARY` and the original `period_id`, `period_year`, and `period_month` aliases together with the original base-table names.


#### D. Rename dependent database objects and verify them

The migration must explicitly audit and, where naming conventions require it, rename:

- primary-key constraint names;
- foreign-key constraint names and their referenced columns;
- unique constraint on employer plus accrual year/month;
- check-constraint names;
- indexes such as payroll-item-by-payroll and materialized-view unique indexes;
- sequences generated by `BIGSERIAL`, including ownership and `pg_get_serial_sequence()` results;
- materialized-view definition, column aliases, and unique index;
- seed SQL, restore scripts, inspection queries, and ORM `__tablename__`/column mappings.

Constraint/index names do not change application behavior, but leaving old `pay_*` names after a full `INC_*` migration creates operational ambiguity. The migration should use explicit `ALTER ... RENAME` operations and post-migration catalog assertions rather than assuming PostgreSQL renames every dependent object automatically.

No new PKs, alternate keys, row version columns, or audit columns are part of this rename. Those would be separate schema design decisions.

#### Migration locking and maintenance strategy

`ALTER TABLE ... RENAME` and column renames take catalog locks. Because the old service cannot safely use the old table/column names after the migration, the cutover must use one of these explicitly approved modes:

1. short maintenance boundary: stop old traffic, apply migration, deploy new ORM, run smoke tests, then enable new traffic;
2. compatibility bridge: deploy code that can operate across old/new names, apply the rename, then remove the bridge — more complex and not preferred for this rename;
3. blue/green with a database compatibility layer: only if live traffic cannot be paused and the layer is designed/tested first.

Recommendation: use a short maintenance boundary coordinated with the Cloud Run deployment. Do not claim this is an online, zero-downtime table rename when the application contract changes at the same time.


The current API is documented under `/payroll`, and Postman contains a top-level payroll service folder plus old service-name variables. The confirmed behavior is:

- service identity/folder/variables become `pf-svc-income`/`pf-svc-income-*`;
- payroll routes remain under `/payroll/*`;
- the detail route is documented as `GET /payroll/{payroll_id}`, not `/payroll/{id}`;
- the concrete URL remains `/payroll/481`, so this is a semantic/OpenAPI parameter rename, not a URL migration;
- DTOs, command fields, repository port fields, and collections should use `payroll_id`/`payrolls` where they represent the `INC_PAYROLL` entity;
- future `/honorarios` or `/income` routes require a separate API design.

No direct `pf-sheets` caller of the current payroll URL was found in the inspected repository source. This must still be confirmed against external Postman workspaces, deployment configuration, logs, and any uncommitted consumer repositories before cutover.

`period_year` and `period_month` should become `accrual_year` and `accrual_month`: the current code uses them to identify the worked/accrual calendar month, while `payment_date` is a separate concept. This avoids calling the payroll entity a period while also avoiding the ambiguity of `payroll_year` when payment can occur in a different month.

### 5.8 Documentation and diagrams

Update the root and service documentation, including:

- root `README.md` and `AGENTS.md` references;
- service `README.md`, `AGENTS.md`, getting-started, database, deployment, API, and workflow docs;
- `pf-db` README/AGENTS/docs/tables/migrations/CI references;
- `pf-common` README, dependency-sync script/docs, and path examples;
- root Postman README, collection, and environments;
- architecture JSON sources, links, labels, URLs, generated HTML, and regeneration metadata;
- deployment runbooks, incident/rollback instructions, and operational URLs.

Historical proposal documents should normally retain the historical name where they describe released behavior, with a short note or related-artifact link if needed. Blind replacement of all historical records would damage auditability.

## 6. Rename-relevant inventory

The read-only inventory confirmed the current service identity, repository/workflow
references, active consumers, and the absence of target `pf-svc-income` resources.
The rename-specific evidence is retained only where it affects package identity,
consumer cutover, schema migration, API compatibility, or rollback.

Security, IAM, Neon, error-baseline, and observability findings discovered during
that inventory are registered as `IMP-001` through `IMP-007`; detailed evidence and
remediation do not belong in this rename investigation.

## 7. Risks and compatibility

| Risk | Severity | Mitigation |
|---|---:|---|
| Consumer cutover misses a current URL or client | High | Inventory consumers, reconcile URLs, update Postman, and run smoke tests |
| Secret rename breaks API clients or runtime | High | Atomic `PF_INCOME_API_KEY` cutover and client migration tests |
| Package rename misses a test, Docker, or import path | Medium | Reference inventory, import smoke test, and complete quality gate |
| `pf-db` schema rename reaches traffic before compatible service code | High | Migration before traffic, maintenance strategy, and rollback validation |
| Historical docs become misleading | Medium | Preserve historical names intentionally and update current docs |
| Rollback cannot restore the old application/schema combination | High | Test the reversible migration and retain the old service for the approved window |

Out-of-scope security, reliability, and operations risks are tracked in the
ecosystem improvement register rather than duplicated here.

## 8. Decisions confirmed and remaining decisions

1. Package name: `income`.
2. Repository target: `mrturo-pf/pf-svc-income`.
3. API key target: `PF_INCOME_API_KEY`, with an atomic cutover and no temporary `PF_PAYROLL_API_KEY` compatibility mode.
4. Internal source split: retain `payroll`; extract shared capabilities into `income`.
5. Physical table rename is approved, including `PAY_PERIOD → INC_PAYROLL` and the complete provisional mapping above.
6. HTTP routes remain under `/payroll/*`; the detail route uses `/payroll/{payroll_id}`.
7. No custom domain/DNS migration exists.
8. The old service remains during the evidence-based rollback window.

Remaining implementation design detail:

- exact package module boundaries and import order;
- exact migration locking/maintenance strategy;
- recommended `accrual_year`/`accrual_month` field rename for current `period_year`/`period_month` fields;
- rollback exit checklist and observation period.

## 9. Inventory and complementary validation

Read-only inventory confirmed that the current `pf-payroll` service remains active
and the target `pf-svc-income` resources do not exist. The rename-specific evidence
covers consumer discovery, URL reconciliation, route/OpenAPI/Postman comparison,
package-boundary decisions, and a local reversible schema-rename probe.

The local probe confirmed the approved rename shape for synthetic data:

- `PAY_*` objects renamed to the approved `INC_*` map;
- `PAY_PERIOD` renamed to `INC_PAYROLL`;
- dependent `period_id` fields renamed to `payroll_id`;
- payroll temporal fields renamed to `accrual_year`/`accrual_month`;
- rows, IDs, foreign keys, materialized-view columns, and rollback were checked.

This probe does not replace a hand-written `pf-db` migration or validation against
representative existing data. The 30-character identifier convention remains a
pending prerequisite and is not active until `pf-db` documentation and validators
are updated; see `IMP-008` in the ecosystem register.

The following findings are intentionally outside this rename investigation and are
tracked in the register:

- `IMP-001`: Scheduler credential exposure and incident response;
- `IMP-002`: Neon credential, backup, restore, and sensitive-dump hardening;
- `IMP-003`: database runtime-role hardening;
- `IMP-004`: `pf-rates` database-secret access review;
- `IMP-005`: deployer/runtime identity hardening and WIF;
- `IMP-006`: reusable HTTP error baseline;
- `IMP-007`: Cloud Run observability and capacity standards.

The rename uses only the resulting gate decisions from those workstreams. Their
raw inventories, credentials, IAM matrices, backup details, error taxonomy, and
operational standards do not belong here.

## 11. Execution order

The work must proceed in three phases. The first phase is read-only and must not
mutate GCP, GitHub, production services, secrets, IAM, databases, or consumers.

### Phase 1 — Safe investigation

1. Confirm consumers and current URLs without assuming either advertised URL is stale.
2. Validate the approved API, package, schema, migration, and rollback contracts.
3. Record unrelated findings in the ecosystem improvement register.

### Phase 2 — Decisions and approvals

Approve the rename-specific package, schema, API, consumer, cutover, and rollback
decisions. Resolve or explicitly accept the registered external gates before
cutover.

### Phase 3 — Controlled changes

Apply only authorized repository, schema, consumer, and deployment changes. Validate
OpenAPI, Postman, synthetic service flows, migration rollback, and the approved
rollback window.

## 11.1 Phase 1 execution record (2026-10-08)

Phase 1 read-only work completed the rename-relevant inventory and confirmed the
remaining gates. Sensitive evidence, security remediation, IAM design, backup and
restore hardening, error classification, and observability work are tracked in
`IMP-001` through `IMP-007` and are not reproduced in this record.

The rename-specific result is:

- current service and consumer references identified;
- target identity does not yet exist;
- route/OpenAPI/Postman comparison completed;
- synthetic schema rename and rollback probe completed;
- implementation and production mutation remain unauthorized.

## 12. Investigation status after complementary validation

The documentation and contract investigation is sufficiently advanced to prepare local code, migration, documentation, and test changes. Before any GCP mutation or cutover, the remaining gates are rotation of the exposed Scheduler credential, a valid GitHub secret inventory, approval of the IAM matrix, final `500/502` classification, URL reconciliation, and approval of the 14-day rollback window.

The recommendation remains to proceed only with local/PR preparation until those gates close. The most economical and safe path remains a short schema maintenance window, a temporary parallel Cloud Run service at scale-to-zero, least-privilege IAM, and a 14-day rollback window extended until representative payroll behavior is proven.

## 12. Conclusion

Proceed with the approved phased identity, schema, package, and domain-language migration to `pf-svc-income`. Keep payroll-specific use cases in `payroll`; extract only stable generic capabilities into `income` with dependency direction `payroll → income`. Rename the physical schema to the approved `INC_*` vocabulary, rename period-oriented DTOs/commands to payroll-oriented names, and use `/payroll/{payroll_id}` for the detail route. Do not implement independent bonuses or honorarios in this rename; prepare the seams they will consume.
