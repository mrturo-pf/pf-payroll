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

Provisional one-to-one mapping for the existing objects is:

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
| `PAY_PRD_HLTH` | `INC_PRD_HLTH` |
| `PAY_PRD_COMP` | `INC_PRD_COMP` |
| `PAY_CONCEPT` | `INC_CONCEPT` |
| `PAY_ITEM` | `INC_ITEM` |
| `PAY_PDF_TEMPLATE` | `INC_PDF_TEMPLATE` |
| `PAY_PDF_TEMPLATE_FIELD` | `INC_PDF_TEMPLATE_FIELD` |
| `PAY_MV_SUMARY` | `INC_MV_SUMARY` |

The exact target names are a design gate before migration. The mapping must respect the existing maximum-name convention, foreign-key dependency order, and the fact that `INC_PAYROLL` is a payroll-period table rather than a generic income ledger.

The migration must update, in dependency order:

- tables and all foreign keys;
- indexes, unique constraints, check constraints, and materialized-view SQL;
- seed SQL and restore/inspection scripts;
- ORM `__tablename__` values and repository SQL;
- tests, fixtures, Postman descriptions, and database docs;
- migration comments and ownership documentation.

The migration must be reversible and tested against a copy of existing data. It must use table renames, not drop/recreate operations, preserve IDs and rows, and verify every foreign key, index, view refresh, seed, rollback, and application query. `pf-db` remains the schema owner; `pf-svc-income` follows after the migration is available.

`PF_DATABASE_URL` and the PostgreSQL database name `pf_db` remain unchanged.

### 5.6.1 Database migration scope beyond table names

The approved migration is not only `PAY_* → INC_*`. It has three explicit layers.

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
| `PAY_PRD_HLTH.period_id` | `INC_PRD_HLTH.payroll_id` | FK now points to a payroll |
| `PAY_PRD_COMP.period_id` | `INC_PRD_COMP.payroll_id` | FK now points to a payroll |
| `PAY_ITEM.period_id` | `INC_ITEM.payroll_id` | FK now points to a payroll |
| `PAY_MV_SUMARY.period_id` | `INC_MV_SUMARY.payroll_id` | Analytics output follows entity language |
| `PAY_PERIOD.period_year` | `INC_PAYROLL.accrual_year` | Current field identifies the worked/devengado month |
| `PAY_PERIOD.period_month` | `INC_PAYROLL.accrual_month` | Current field identifies the worked/devengado month |
| `PAY_MV_SUMARY.period_year` | `INC_MV_SUMARY.accrual_year` | View mirrors the payroll accrual month |
| `PAY_MV_SUMARY.period_month` | `INC_MV_SUMARY.accrual_month` | View mirrors the payroll accrual month |

The base table's PK remains `id`, not `payroll_id`, because changing every surrogate-key column would create unnecessary ORM, sequence, FK, and client churn. The semantic name belongs in DTOs, commands, route parameters, and FK columns; the base PK can remain the conventional `id`.

#### C. Rename dependent database objects and verify them

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

## 6. Live inventory completed 2026-10-05

### GitHub

Read-only GitHub inventory succeeded after following the repository's proxy/certificate prerequisite:

- current repository: public `mrturo-pf/pf-payroll` on `main`;
- active workflows: Debug CI, CI / Deploy to Cloud Run, CI / Expire Stale Deployment Approvals, and GCP Setup - pf-payroll;
- repository-level secrets visible by name: `GCP_SA_KEY`, `GH_PAT`;
- repository variables visible: none;
- environments: `GCP` and `production`; production has a required-reviewer protection rule;
- branch `main` is not protected according to the GitHub API; the inspected hooks query returned no repository hooks.

Environment-scoped secret names remain to be checked through the environments API, while branch protection is explicitly absent and no repository hooks were returned by the inspected query. External consumers still require a more detailed inventory before the repository rename. Secret values were not requested or exposed.

Recent deployment evidence: run `37255381815` succeeded on 2026-10-05 for commit `db9e94d1e4802b62a799c6e277e235aa468df58f`. This is historical evidence of the current pipeline, not evidence for the rename.

### GCP

Read-only inventory was extended through 2026-10-07 using the authenticated project `coreassistant-474022` and region `us-central1`. The evidence was supplied by the operator and is intentionally not retained as a repository artifact.

Confirmed current state:

- Cloud Run service `pf-payroll` is healthy, with 100% traffic on `pf-payroll-00056-lgk`;
- current URL is `https://pf-payroll-yqd4p7fzaa-uc.a.run.app`;
- Cloud Run also advertises the historical URL `https://pf-payroll-646185261155.us-central1.run.app`, and logging confirms requests to both URLs;
- active image is pinned by digest under Artifact Registry repository `pf-payroll`;
- current runtime has CPU `1`, memory `512Mi`, concurrency `80`, timeout `300s`, startup TCP probe on port `8080`, no VPC connector, and no explicit minimum scale;
- service annotations expose `maxScale=20` at service level and `maxScale=2` at revision template level; this discrepancy must be preserved or intentionally resolved during deployment design;
- Artifact Registry `pf-payroll` exists and is approximately 3.6 GB; `pf-svc-income` does not exist;
- service account `pf-payroll@coreassistant-474022.iam.gserviceaccount.com` exists; target account `pf-svc-income@coreassistant-474022.iam.gserviceaccount.com` does not;
- `PF_PAYROLL_API_KEY`, `PF_DATABASE_URL`, and `PF_RATES_API_KEY` exist; `PF_INCOME_API_KEY` does not;
- no VPC connector, forwarding rule, DNS managed zone, Certificate Manager configuration, alert policy, dashboard, log-based metric, or uptime check was found in the collected outputs; Certificate Manager was deliberately not queried because its API was disabled;
- Scheduler has one enabled job, `gcp-scheduler-runner-chile`, which targets `gcp-scheduler-runner`, not `pf-payroll`;
- the safe Scheduler output no longer contains headers or bodies, but the first inventory exposed a literal `X-API-Key`; that credential must be rotated separately and the old artifact must not be shared.

Artifact Registry and Logging are therefore no longer wholly blocked. The current repository metadata and log summaries are available, while repository IAM and some target-resource checks remain permission/nonexistence gaps.

### Consumers

Cloud Logging inventory for the last 30 days confirms real consumers, primarily Postman runtimes and `curl`. Requests reached both the historical and current Cloud Run URLs. Observed route families include `/health`, `/payroll/*`, imports, PDF preview, spreadsheet/export, payroll review/deflation, and `/reference-data/*`.

The reduced summaries contain successful requests plus `400`, `403`, `404`, `409`, `422`, `500`, `502`, and `302` responses. These are a cutover baseline, not yet a defect classification. Before rollout, classify expected validation/not-found/authentication responses separately from infrastructure or downstream failures, especially the `500`/`502` responses on import, spreadsheet, summary, and reference-data operations.

No direct `pf-sheets` caller of the payroll service was found; `pf-sheets` calls `pf-rates`. External clients, Postman workspace values, and environment-scoped GitHub secrets still require explicit inventory.



### Facts

- The three repositories are independent Git repositories with clean `main` worktrees.
- The payroll repository remote is `mrturo-pf/pf-payroll`.
- The service uses package namespace `payroll` and API key name `PF_PAYROLL_API_KEY`.
- The reusable deployment workflow derives GCP resource names from `repo_name`.
- The database owns `PAY_*` tables and the service connects via generic `PF_DATABASE_URL`.
- The root Postman GCP environment contains a deployed payroll URL.

### Inferences

- A true service rename should include Python distribution/package identity, not only the folder, otherwise the code will continue presenting itself as payroll internally.
- A safe GCP migration requires parallel resources, not an in-place Cloud Run rename.
- Existing `/payroll` routes remain; the detail path parameter is renamed to `payroll_id` in code/OpenAPI/docs.

### Assumptions requiring verification

- Whether consumers outside these repositories use the old repository URL, Cloud Run URL, API key, or Postman variables;
- whether GitHub repository rename preserves all settings, secrets, environments, branch protections, webhooks, and Actions history as desired;
- exact IAM bindings for any future target Artifact Registry repository and target service account;
- whether the broad current project roles on `pf-payroll` can be reduced and separated between runtime and deployer identities;
- whether `pf-rates` genuinely needs access to `PF_DATABASE_URL` in addition to its rates secret;
- whether any database role, RLS policy, backup/export job, or operational script embeds `PAY_*` ownership or `pf-payroll`;
- rollback window length, observation period, and criteria for cleanup;
- classification of observed `500`/`502` responses before establishing the new service baseline.

## 7. Risks and compatibility

| Risk | Severity | Mitigation |
|---|---:|---|
| Deploying new Cloud Run service without migrating URL consumers | High | Inventory consumers, parallel deploy, smoke tests, explicit cutover |
| Secret rename breaks API clients or runtime | High | Atomic `PF_INCOME_API_KEY` cutover; old secret retained only for operational rollback; test client migration |
| `pf-common` reusable workflow receives wrong repository name | High | Update caller and shared examples; run PR CI before production |
| Package rename misses a test/Docker/import path | Medium | Mechanical reference inventory, import smoke test, complete quality gate |
| `pf-db` schema rename breaks an old ORM deployment if it is not coordinated | High | Migration must land before new code; choose maintenance/compatibility strategy; test rollback |
| Historical docs become misleading | Medium | Preserve historical names in historical records; update current docs and add alias notes |
| Old URL is assumed redirected forever | Medium | Explicitly test redirect and maintain old service during rollback window |
| New service account lacks secret or DB permissions | High | IAM diff and runtime health/API smoke tests before cutover |
| Literal credential exposed in inventory output | Critical | Treat the Scheduler `X-API-Key` from inventory 01 as compromised; rotate through an authorized operational procedure, redact/remove old artifacts, and do not commit raw exports |
| Broad current service-account permissions copied to new runtime | High | Separate runtime and deployer identities; produce a least-privilege IAM diff before creating target resources |
| Existing `500`/`502` responses mistaken for migration regressions | High | Classify baseline failures and repeat representative synthetic smoke tests before and after cutover |
| `pf-rates` access to shared database secret is unnecessary | High | Confirm dependency and reduce secret IAM if not required |
| Concurrent releases race during rename | Medium | Follow active-run cancellation rule per repository before authorized push |

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

## 9. Inventory evidence record (2026-10-07)

The operator supplied a third read-only GCP inventory during the investigation. Its local commands and downloaded outputs are intentionally excluded from this repository; this document records only the resulting facts and decisions.


### Confirmed current resource facts

- `pf-payroll` Artifact Registry exists as a Docker standard repository of approximately 3.6 GB.
- `pf-svc-income` Artifact Registry does not exist.
- Current Cloud Run image is pinned by digest `sha256:70cc7b2a4867c474970a9723a6b2526b1054a79725258dfc3801acc3ca7dc363`.
- Current service uses `1` CPU, `512Mi` memory, concurrency `80`, timeout `300s`, and TCP startup probe on port `8080`.
- Current runtime service account has project roles `roles/artifactregistry.writer`, `roles/cloudsql.client`, `roles/iam.serviceAccountUser`, `roles/run.admin`, and `roles/secretmanager.secretAccessor`.
- The target service account does not exist.
- The target API key secret does not exist; the old API key has one enabled version.
- `PF_DATABASE_URL` and `PF_RATES_API_KEY` are accessible to both `pf-payroll` and `pf-rates` according to the collected secret IAM policies. This must be justified or reduced.
- Scheduler has one enabled job targeting `gcp-scheduler-runner`, not `pf-payroll`.
- No current alerting, dashboards, log metrics, uptime checks, VPC connectors, forwarding rules, or DNS managed zones were present in the collected outputs.

### Security handling

Inventory 01 included a literal Scheduler `X-API-Key` in exported files. Inventory 03 avoided repeating it, but the credential should be considered compromised until rotated. The raw inventory and any archive containing it must not be shared or committed. No secret values were intentionally read from Secret Manager during inventories 01–03.

### Investigation status after inventory 03

The GCP evidence phase is substantially complete for the existing service, but implementation is not authorized. Remaining blockers are IAM design, Scheduler credential rotation, error classification, environment-scoped GitHub secret inventory, Postman URL reconciliation, database migration validation, and explicit approval for resource creation/deployment.

## 10. Complementary investigation: contracts, GitHub, IAM, and local migration (2026-10-07)

### HTTP, OpenAPI, documentation, and Postman

Static inspection of `modules/pf-payroll/src/payroll/interfaces/api/routes/` found 27 application routes:

- `GET /health`;
- 15 payroll/import/export/query/delete routes;
- 5 template routes;
- 6 employer/contract/reference-data routes.

`modules/pf-payroll/docs/api.md` documents the 27 application routes and the three FastAPI-generated routes (`/docs`, `/redoc`, `/openapi.json`), for 30 documented entries. No implemented route was missing from `docs/api.md` in this static comparison.

The Postman collection contains 28 requests under `pf-payroll`, covering the functional operations. Four requests use literal IDs (`/payroll/481`, `walmart-chile-v1`, `acme-chile-v1`) where mechanical comparison cannot automatically match them to `{period_id}`/`{template_id}` parameters; semantically, they cover those operations. This must be normalized during the rename, not solved through blind replacement.

The current contract still uses `period_id` in routes, documentation, and Postman. The proposed `payroll_id` implementation requires simultaneous updates to:

- route parameters and OpenAPI;
- DTOs, commands, and ports;
- `docs/api.md`;
- Postman variables, examples, and descriptions;
- contract tests.

Concrete URL names do not change: `/payroll/481` remains `/payroll/481`.

### GitHub: environments, secrets, and workflow history

The read-only query of `mrturo-pf/pf-payroll` confirmed:

- public, active repository with default branch `main`;
- active workflows: Debug CI, CI / Deploy to Cloud Run, CI / Expire Stale Deployment Approvals, and GCP Setup - pf-payroll;
- environments `GCP` and `production`;
- `production` has a `required_reviewers` protection rule; `GCP` showed no protection rules;
- `main` is not protected;
- recent successful runs exist, including deploy run `37255381815` for SHA `db9e94d1...`;
- workflow history remains visible in the current repository.

Repository- or environment-scoped secret names could not be confirmed: GitHub secret endpoints returned HTTP 500. An empty `gh secret list` result must not be interpreted as absence of secrets. This query must be repeated with working permissions or an operational endpoint, without reading values. It is also unproven that GitHub preserves secrets, environments, reviewers, hooks, and Actions history exactly after a rename; that must be verified after the operation if authorized.

### Proposed target IAM matrix

The following matrix is design, not an executed grant:

| Identity | Need | Target permission |
|---|---|---|
| Runtime `pf-svc-income` | Read specific secrets | `roles/secretmanager.secretAccessor` only on `PF_DATABASE_URL`, `PF_RATES_API_KEY`, and `PF_INCOME_API_KEY` |
| Runtime `pf-svc-income` | Cloud SQL database, only if the backend is actually Cloud SQL | `roles/cloudsql.client`; omit it for an external database without a connector |
| Runtime `pf-svc-income` | Call `pf-rates` | Invoker/application configuration according to the current model; do not assume database access to `pf-rates` |
| GitHub deployer | Publish images | `roles/artifactregistry.writer` on the target repository |
| GitHub deployer | Deploy Cloud Run | Approved `roles/run.admin` scope and `roles/iam.serviceAccountUser` only on the runtime account |
| Setup/administration | Create resources, secrets, and bindings | Separate temporary or administrative identity; never reuse it as runtime |

The current `pf-payroll` account combines all of those roles at project level. It must not be copied automatically. `pf-rates` has access to `PF_DATABASE_URL` and `PF_RATES_API_KEY`; database-secret access requires explicit justification or removal.

### Proposed rollback and exit criteria

Operational proposal for approval: retain the old service, image, account, and secret for **14 calendar days and until at least one representative payroll flow completes**, whichever is longer. Extending the period to 30 days requires an explicit decision; Cloud Run remains at scale-to-zero to control cost.

Close the window only when:

- all known consumers use the new URL and `PF_INCOME_API_KEY`;
- `/health`, reads, JSON import, spreadsheet import, PDF preview, export, and reference-data requests pass with synthetic data;
- the new service correctly reads and writes `INC_*`;
- there is no unexplained increase in `500`/`502`, latency, or `pf-rates` errors;
- a rollback has been tested, or the old service is explicitly documented as outside rollback scope;
- old operational references have been retired and the user authorizes cleanup.

### Local `pf-db` migration validation

A local PostgreSQL 16 instance was started with `modules/pf-db/docker-compose.yml`, and Alembic migrations were applied through `0016` on a fresh database. The schema confirmed the 17 `PAY_*` tables, `PAY_MV_SUMARY`, sequences, constraints, and foreign keys.

A local transactional test then used a synthetic employer and payroll:

- renamed the complete table map to `INC_*`, including `PAY_PERIOD → INC_PAYROLL` and `PAY_MV_SUMARY → INC_MV_SUMARY`;
- renamed `period_id → payroll_id` in dependent tables;
- renamed `period_year/month → accrual_year/month` in payroll and the materialized view;
- verified row and ID preservation;
- verified foreign keys to `INC_PAYROLL` and `INC_CONCEPT`;
- verified columns on the renamed materialized view;
- executed `ROLLBACK`;
- verified that the synthetic employer was not persisted and `alembic_version` remained `0016`.

Result: **the rename operation is technically viable as a reversible local transaction**. This test does not replace a hand-written Alembic migration and does not yet validate explicit names for every constraint/index, sequence ownership, refresh/data semantics, or locks against a representative copy; those remain `pf-db` implementation requirements.

### Initial classification of observed errors

The Cloud Logging baseline contains:

- `400`, `422`: likely request validation, subject to synthetic-case confirmation;
- `403`: authentication/authorization or incorrect API key, requiring traceability;
- `404`: missing IDs/routes/templates and `/`, probably expected in some cases;
- `409`: natural-key or state conflicts, to be reviewed as contract behavior;
- `500`: appears on payroll, summary, spreadsheet, and reference-data routes; must not be assumed expected;
- `502`: appears on JSON/rows/spreadsheet imports and must be separated into downstream failures versus service/data errors;
- `302`: an HTTP-without-TLS call that should be removed from consumers.

This classification is a working hypothesis, not a root-cause conclusion. Acceptance must include cases that exercise each class.

## 11. Investigation status after complementary validation

The documentation and contract investigation is sufficiently advanced to prepare local code, migration, documentation, and test changes. Before any GCP mutation or cutover, the remaining gates are rotation of the exposed Scheduler credential, a valid GitHub secret inventory, approval of the IAM matrix, final `500/502` classification, URL reconciliation, and approval of the 14-day rollback window.

The recommendation remains to proceed only with local/PR preparation until those gates close. The most economical and safe path remains a short schema maintenance window, a temporary parallel Cloud Run service at scale-to-zero, least-privilege IAM, and a 14-day rollback window extended until representative payroll behavior is proven.

## 12. Conclusion

Proceed with the approved phased identity, schema, package, and domain-language migration to `pf-svc-income`. Keep payroll-specific use cases in `payroll`; extract only stable generic capabilities into `income` with dependency direction `payroll → income`. Rename the physical schema to the approved `INC_*` vocabulary, rename period-oriented DTOs/commands to payroll-oriented names, and use `/payroll/{payroll_id}` for the detail route. Do not implement independent bonuses or honorarios in this rename; prepare the seams they will consume.
