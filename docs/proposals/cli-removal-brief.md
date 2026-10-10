# CLI Removal and Safe Use-Case Cleanup Brief

**Date:** 2026-10-04
**Repository:** `pf-payroll`
**Related analysis:** [`../investigations/cli-removal.md`](../investigations/cli-removal.md)
**Status:** released; implementation completed. This brief is retained as historical
context for the CLI removal; the CLI is not a supported interface.

## Objective

Remove the entire `pf-payroll` CLI because the flow is not used and creates unnecessary
maintenance surface.

The cleanup must be conservative with business logic:

- remove every CLI command and CLI adapter;
- remove only helpers, wiring, and tests that become unused;
- preserve every use case used by the HTTP API or internal processing;
- preserve HTTP contracts and the behavior of imports, PDF previews, queries, taxes, and
  contributions;
- update documentation and architecture so they do not describe a CLI that no longer
  exists.

## Expected result

After the change:

- `payroll.interfaces.cli.main` no longer exists;
- the commands `health`, `import-payroll`, `template-test`, `summary`, `period-detail`,
  and `plan-snapshots` no longer exist;
- the `make import-payroll` target no longer exists;
- the HTTP API retains all current routes;
- automatic import processing continues to calculate and validate contributions, taxes,
  and net pay;
- use cases with HTTP or internal consumers remain available;
- dead-code and mypy checks report no remnants of the removed adapter.

## Command-by-command scope

| Command to remove | Removal | Logic to preserve |
| --- | --- | --- |
| `health` | Remove the Typer function | HTTP `GET /health` |
| `import-payroll <file>` | Remove command, async helper, and Make target | `ImportPayroll`, `ProcessImportedPayrollPeriods`, `ComputeIncomeTax`, `ContributionComputationService`, and HTTP import routes |
| `template-test <pdf>` | Remove command and async helper | `PreviewPdfImport`, extractor, template repository, and `POST /payroll/pdf-preview` |
| `summary` | Remove command and async helper | `PayrollQueries` and `GET /payroll` |
| `period-detail <period_id>` | Remove command and async helper | `PayrollQueries` and `GET /payroll/{period_id}` |
| `plan-snapshots` | Remove command and async helper | `ReferenceDataQueries` and HTTP reference-data routes |

## Component inventory: preserve versus remove

### Components that must remain

These components have confirmed non-CLI consumers:

```text
src/payroll/application/use_cases/import_payroll.py
src/payroll/application/use_cases/process_imported_payroll_periods.py
src/payroll/application/use_cases/compute_income_tax.py
src/payroll/application/use_cases/preview_pdf_import.py
src/payroll/application/use_cases/payroll_queries.py
src/payroll/application/use_cases/reference_data.py
src/payroll/application/services/contribution_computation.py
```

Reasons:

- `ImportPayroll` is used by `/payroll/import/spreadsheet` and `/payroll/import/json`.
- `ProcessImportedPayrollPeriods` is used by the HTTP import routes.
- `ComputeIncomeTax` is constructed and executed by `ProcessImportedPayrollPeriods`.
- `ContributionComputationService` is part of automatic period processing.
- `PreviewPdfImport` is used by `/payroll/pdf-preview`.
- `PayrollQueries` is used by `/payroll` and `/payroll/{period_id}`.
- `ReferenceDataQueries` is used by institution, plan, cap, and concept routes.

The ports, DTOs, repositories, and clients required by these use cases must remain as
well.

### Wiring to remove only after consumer verification

The following dependency factory appears to become orphaned after CLI removal:

```text
get_compute_income_tax_use_case()
```

Location:

```text
src/payroll/interfaces/api/dependencies.py
```

The factory is not connected to a current HTTP route. The production tax calculation is
performed by the `ComputeIncomeTax` instance created inside
`ProcessImportedPayrollPeriods`.

Required procedure:

1. confirm that the factory has no consumers other than historical or CLI-oriented tests;
2. remove the factory and imports exclusive to it;
3. update or remove the isolated factory test;
4. run dead-code, mypy, and the complete test suite;
5. do not remove `ComputeIncomeTax` or its DTOs, ports, or repository methods.

### CLI adapter files to remove

Review and remove:

```text
src/payroll/interfaces/cli/main.py
src/payroll/interfaces/cli/__init__.py
tests/unit/interfaces/test_cli_main.py
```

Before deleting `__init__.py`, confirm that it contains no exports or behavior beyond a
package marker.

## Implementation phases

### Phase 0: freeze the baseline and inventory consumers

1. Work in `modules/pf-payroll`, not from the `pf-base` root.
2. Confirm the repository is clean or separate pre-existing changes.
3. Record the base commit.
4. Confirm the current HTTP routes through route definitions and OpenAPI:
   - `GET /health`;
   - `POST /payroll/import/spreadsheet`;
   - `POST /payroll/import/json`;
   - `POST /payroll/pdf-preview`;
   - `GET /payroll`;
   - `GET /payroll/{period_id}`;
   - reference-data routes.
5. Search the whole ecosystem for:

```text
interfaces.cli
import-payroll
template-test
period-detail
plan-snapshots
```

6. Record the references so the final audit can distinguish removed references from
   intentional historical notes.

### Phase 1: remove the CLI surface

1. Remove `src/payroll/interfaces/cli/main.py`.
2. Remove `src/payroll/interfaces/cli/__init__.py` if it is only a package marker.
3. Remove `tests/unit/interfaces/test_cli_main.py`.
4. Do not remove anything from `application/` at this stage.
5. Do not modify HTTP routes.

This removes the CLI entrypoint as one coherent adapter instead of leaving partially
functional commands behind.

### Phase 2: clean the Makefile

Remove from `modules/pf-payroll/Makefile`:

```make
.PHONY: import-payroll
import-payroll: ...
```

Also review documentation references to:

```text
make cli
make import-payroll
```

Do not create a replacement CLI. The supported import interfaces are now:

```text
POST /payroll/import/spreadsheet
POST /payroll/import/json
```

### Phase 3: remove orphaned wiring

After removing the CLI:

1. search for consumers of `get_compute_income_tax_use_case`;
2. remove it only if it is used solely by the historical test/factory path;
3. preserve API factories, including:
   - `get_transactional_import_payroll_use_case`;
   - `get_transactional_process_imported_payroll_periods_use_case`;
   - `get_preview_pdf_import_use_case`;
   - `get_payroll_queries`;
   - `get_reference_data_queries`;
4. run `make dead-code` and `make typecheck` before continuing.

Do not remove a function merely because its name sounds CLI-related. Consumer searches
and integration tests are the source of truth.

### Phase 4: update documentation and architecture

Update:

```text
docs/api.md
docs/getting-started.md
docs/development.md
docs/payroll-workflow.md
```

Documentation actions:

- remove the CLI section from `docs/api.md`;
- remove the command list;
- remove the `make import-payroll` example;
- remove instructions that run `python -m payroll.interfaces.cli.main`;
- document the HTTP routes as the supported interfaces;
- preserve the HTTP import and PDF-preview documentation.

Update architecture references:

```text
../../architecture/pf-payroll.architecture.json
../../architecture/pf-payroll.html
```

First determine whether the HTML is generated from the JSON. If a generator exists,
regenerate the HTML instead of editing a generated artifact independently.

Update investigation documents that describe the CLI as a current surface. Historical
references may remain only when explicitly labeled historical.

### Phase 5: validate HTTP contracts and internal flows

Run:

```bash
make check
```

The validation must cover:

- lint and formatting;
- dead-code;
- mypy;
- duplicate-code checks;
- unit tests;
- integration tests;
- required coverage.

Verify these routes remain available:

```text
GET /health
POST /payroll/import/spreadsheet
POST /payroll/import/json
POST /payroll/pdf-preview
GET /payroll
GET /payroll/{period_id}
GET /payroll/pension-institutions
GET /payroll/health-institutions
GET /payroll/pension-plans
GET /payroll/health-plans
GET /payroll/contribution-caps
GET /payroll/payroll-concepts
```

Also verify that `ProcessImportedPayrollPeriods` still constructs and executes
`ComputeIncomeTax`, and that imports retain their transactional behavior.

### Phase 6: final reference audit

Run a repository-wide search:

```bash
grep -R \
  -E "interfaces\.cli|import-payroll|template-test|period-detail|plan-snapshots|make cli" \
  .
```

Expected result:

- no active references to `interfaces.cli`;
- no supported CLI commands documented;
- no Make targets invoking the CLI;
- any remaining match is explicitly historical or part of the removal diff.

Confirm that these names do not remain in a CLI-only context:

```text
ComputeIncomeTaxCommandDTO
ImportPayroll
PayrollQueries
PreviewPdfImport
ProcessImportedPayrollPeriods
ReferenceDataQueries
```

The same names should continue to appear where the HTTP API or internal processing uses
them.

### Phase 7: review and commit

Before committing:

1. run `git diff --check`;
2. verify that no application use case with active consumers was deleted;
3. confirm `docs/api.md` still matches the real API;
4. confirm Postman requires no changes because no HTTP endpoint was removed;
5. review the complete deleted/modified-file summary;
6. create a Conventional Commit, for example:

```text
refactor(payroll): remove unused CLI surface
```

Push and pipeline monitoring remain subject to the repository's normal operational
approval rules.

## Acceptance matrix

| Criterion | Expected result |
| --- | --- |
| CLI removed | `src/payroll/interfaces/cli` does not exist |
| Make target removed | `make import-payroll` does not exist |
| HTTP use cases preserved | Import, preview, query, and reference-data tests pass |
| Automatic calculations preserved | Imports still execute tax and contribution processing |
| API unchanged | OpenAPI retains all functional operations |
| Tests | `make check` passes |
| Dead code | No orphaned factories or imports remain |
| Documentation | No document presents CLI as a supported interface |
| Architecture | Removed CLI adapter is not registered |
| Ecosystem | No `pf-*` repository calls the CLI |

## Risks and mitigations

### Unversioned external manual use

Git cannot prove that nobody outside the repositories invokes the CLI. The stated product
decision confirms that this flow is not used. The mitigation is to clearly document HTTP
as the supported interface.

### Accidental deletion of business logic

Do not delete files under `application/use_cases/` unless consumer searches, dead-code,
and tests confirm that they are orphaned. All major use cases associated with the CLI
currently have confirmed non-CLI consumers.

### Stale documentation

Update `docs/api.md`, development documentation, workflow documentation, and architecture
in the same change. Run the final reference audit.

### Accidental API change

Do not modify HTTP routes or schemas in this work. Compare the resulting OpenAPI surface
with the existing documented API.

## Final decision

Apply complete CLI removal while preserving business logic:

- remove every CLI command;
- remove the `make import-payroll` target;
- remove CLI-only tests and documentation;
- remove `get_compute_income_tax_use_case` only if consumer searches confirm it is orphaned;
- preserve `ImportPayroll`, `ProcessImportedPayrollPeriods`, `ComputeIncomeTax`,
  `ContributionComputationService`, `PreviewPdfImport`, `PayrollQueries`, and
  `ReferenceDataQueries`;
- validate the complete HTTP API and internal processing before commit and push.
