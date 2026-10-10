# Impact of Removing the `pf-payroll` CLI Surface

**Date:** 2026-10-04
**Scope:** `pf-payroll` and consumers within the `pf-*` ecosystem
**Status:** released; historical decision record. The CLI removal described here has
been implemented. Command listings and implementation paths below describe the
pre-removal state and are not supported interfaces.

## Executive summary

The entire `pf-payroll` CLI surface can be removed without breaking automated `pf`
ecosystem flows, provided that only the CLI adapter is removed and use cases still used
by the HTTP API or internal processing are preserved.

The HTTP API already covers the functional capabilities that provide business value:

```text
POST /payroll/import/spreadsheet
POST /payroll/import/json
POST /payroll/pdf-preview
GET  /payroll
GET  /payroll/{period_id}
GET  /payroll/pension-plans
GET  /payroll/health-plans
```

The proposed removal affects an administrative/development interface that is no longer
used. It does not require removing import logic, PDF preview logic, queries, tax
calculation, or period processing.

## Decision

Remove all `pf-payroll` CLI commands because they are not part of the current workflow
and create unnecessary maintenance surface.

The removal must be surgical:

1. remove the `interfaces/cli` adapter;
2. remove the CLI-specific Make target;
3. remove CLI-only tests and documentation;
4. remove orphaned wiring with no remaining consumers;
5. preserve use cases and services with HTTP or internal consumers;
6. run the complete test suite before publishing the change.

## Current CLI commands

The implementation is located at:

```text
src/payroll/interfaces/cli/main.py
```

The exposed commands are:

| Command | Main implementation | HTTP equivalent | Removal risk |
| --- | --- | --- | --- |
| `health` | Responds with `ok` directly; uses no use case | `GET /health` | None for automation |
| `import-payroll <file>` | `ImportPayroll` + `ProcessImportedPayrollPeriods` | `POST /payroll/import/spreadsheet` and `/payroll/import/json` | Medium for manual use; low for automation |
| `template-test <pdf>` | `PreviewPdfImport` | `POST /payroll/pdf-preview` | Medium for manual debugging; low for the product |
| `summary` | `PayrollQueries.list_period_summaries()` | `GET /payroll` | Low/none |
| `period-detail <period_id>` | `PayrollQueries.get_period_detail()` | `GET /payroll/{period_id}` | Low/none |
| `plan-snapshots` | `ReferenceDataQueries` for pension and health plans | `GET /payroll/pension-plans` and `/payroll/health-plans` | Low/none |

No consumers were found in `pf-rates`, `pf-sheets`, or `pf-db` that invoke the CLI
commands. No invocations were found in workflows or ecosystem scripts outside
`pf-payroll` either.

This cannot prove that no external scripts exist outside the repositories, but it does
confirm that no versioned consumers exist within the `pf` ecosystem.

## Detailed command analysis

### `health`

Current implementation:

```python
@app.command()
def health() -> None:
    typer.echo("ok")
```

It does not build sessions, repositories, or use cases. It has no versioned internal
consumers. The HTTP service retains its own `GET /health` endpoint.

**Conclusion:** removing it affects no use case or automated flow.

### `import-payroll <file>`

The CLI flow performs these operations:

1. opens a transactional session;
2. runs `ImportPayroll.from_bytes()` to parse CSV/XLSX;
3. runs `ProcessImportedPayrollPeriods.execute()`;
4. validates contribution and net-pay reconciliation;
5. commits only when the import is fully validated;
6. rolls back the transaction on a genuine conflict.

The same capability exists over HTTP:

```text
POST /payroll/import/spreadsheet
```

This route receives the file as multipart data and uses `ImportPayroll.from_bytes()` and
`ProcessImportedPayrollPeriods` through transactional dependencies.

There is also:

```text
POST /payroll/import/json
```

This route uses `ImportPayroll.from_rows()` for structured periods, such as periods that
come from a human-edited PDF preview. It shares the same processing and reconciliation
pipeline.

Both routes support the following `mode` semantics:

```text
mode=commit
mode=validate
```

The HTTP implementation preserves transaction control, conflict validation, automatic
contribution and tax calculation, and rollback where required.

#### Related use cases

The following must not be removed:

- `ImportPayroll`: used by `/payroll/import/spreadsheet` and `/payroll/import/json`.
- `ProcessImportedPayrollPeriods`: used by the HTTP routes and responsible for automatic
  processing.
- `ComputeIncomeTax`: instantiated by `ProcessImportedPayrollPeriods`.
- `ContributionComputationService`: used during import post-processing.

**Conclusion:** the CLI command can be removed, but these use cases cannot.

### `template-test <pdf>`

The command builds `PreviewPdfImport` with:

- `TemplatePdfPayrollExtractor`;
- `SqlAlchemyTemplateRepository`;
- a read-only session.

It displays the matched template, the number of rows, unresolved rows, and the full
preview JSON.

The equivalent product capability exists at:

```text
POST /payroll/pdf-preview
```

The HTTP route uses the same `PreviewPdfImport` use case and the same active templates
stored in `PAY_PDF_TEMPLATE` and `PAY_PDF_TEMPLATE_FIELD`. The API also accepts multiple
PDFs in one request.

The difference is ergonomics:

- the CLI provides an immediate summary for local debugging;
- the API returns a structured JSON response;
- both execute the same extractor and query the same templates;
- neither persists the preview.

#### Related use case

The following must not be removed:

```text
PreviewPdfImport
```

It is used by `POST /payroll/pdf-preview`.

The extractor and template repository must also remain because they are part of the HTTP
endpoint.

**Conclusion:** removing the CLI command does not remove PDF preview capability from the
product; it removes only the manual terminal helper.

### `summary`

The command calls:

```text
PayrollQueries.list_period_summaries()
```

The query capability is available through:

```text
GET /payroll
```

The current endpoint returns the nested period, employer, and amount format.

`PayrollQueries` must remain because the HTTP query routes also use it.

**Conclusion:** remove the command and CLI helper; preserve `PayrollQueries`.

### `period-detail <period_id>`

The command calls:

```text
PayrollQueries.get_period_detail(period_id)
```

The equivalent capability is exposed through:

```text
GET /payroll/{period_id}
```

The API uses the same `PayrollQueries` use case. Removing the command does not affect the
HTTP query or the use case.

**Conclusion:** remove the command and CLI helper; preserve `PayrollQueries`.

### `plan-snapshots`

The command calls:

```text
ReferenceDataQueries.list_pension_plans()
ReferenceDataQueries.list_health_plans()
```

The HTTP equivalents are:

```text
GET /payroll/pension-plans
GET /payroll/health-plans
```

`ReferenceDataQueries` also serves these other reference-data routes:

```text
GET /payroll/pension-institutions
GET /payroll/health-institutions
GET /payroll/contribution-caps
GET /payroll/payroll-concepts
```

**Conclusion:** remove the command and CLI helper; preserve `ReferenceDataQueries`.

## Use cases and services that must remain

| Component | Confirmed non-CLI consumer | Decision |
| --- | --- | --- |
| `ImportPayroll` | HTTP import routes | Preserve |
| `ProcessImportedPayrollPeriods` | HTTP routes and import pipeline | Preserve |
| `ComputeIncomeTax` | `ProcessImportedPayrollPeriods` | Preserve |
| `ContributionComputationService` | Automatic import processing | Preserve |
| `PreviewPdfImport` | `POST /payroll/pdf-preview` | Preserve |
| `PayrollQueries` | `GET /payroll` and `GET /payroll/{period_id}` | Preserve |
| `ReferenceDataQueries` | HTTP reference-data routes | Preserve |
| `DeflateAmounts` | Existing internal logic | Do not remove in this change |

### Special case: `get_compute_income_tax_use_case`

A dependency factory exists at:

```text
src/payroll/interfaces/api/dependencies.py
```

The consumer analysis showed that:

- the function is defined in `dependencies.py`;
- it is not connected to a current HTTP route;
- it appears in an infrastructure test;
- the actual import-time calculation is built inside
  `ProcessImportedPayrollPeriods`.

After CLI removal, this dependency appears to become orphaned. The factory, its exclusive
imports, and its dedicated test may be removed, but the `ComputeIncomeTax` use case and
its DTOs, ports, and repository methods must not be removed because automatic import
processing uses them.

Confirm the removal with `make dead-code`, `make typecheck`, and the complete test suite.

## Files likely affected

### Removal

Review and remove if they contain no other responsibility:

```text
src/payroll/interfaces/cli/main.py
src/payroll/interfaces/cli/__init__.py
tests/unit/interfaces/test_cli_main.py
```

### Modification

```text
Makefile
```

Remove the target:

```make
make import-payroll CSV_FILE=...
```

Also review CLI wiring in `dependencies.py` and remove only what has no remaining
consumers.

### Documentation

Update or remove the CLI section in:

```text
docs/api.md
docs/getting-started.md
docs/development.md
docs/payroll-workflow.md
```

Also update architecture references that register:

```text
src/payroll/interfaces/cli/main.py
```

In particular:

```text
architecture/pf-payroll.architecture.json
architecture/pf-payroll.html
```

Determine whether the HTML is generated from the JSON or maintained by a dedicated
process before changing either file.

## What breaks and what does not

### It breaks

- `python -m payroll.interfaces.cli.main <command>`.
- The `make import-payroll CSV_FILE=...` target.
- CLI adapter tests.
- The manual terminal import workflow.
- The manual PDF-template debugging helper.
- Administrative terminal queries (`summary`, `period-detail`, `plan-snapshots`).

### It does not break

- `GET /health`.
- `POST /payroll/import/spreadsheet`.
- `POST /payroll/import/json`.
- `POST /payroll/pdf-preview`.
- `GET /payroll`.
- `GET /payroll/{period_id}`.
- Reference-data routes.
- Automatic tax calculation.
- Automatic contribution calculation.
- Transactional import post-processing.
- The `pf-payroll` integration with `pf-rates`.
- `pf-sheets`, `pf-db`, and `pf-common`.
- CI/CD pipelines, provided that obsolete CLI documentation and tests are removed too.

## Required validation after the change

Run in `pf-payroll`:

```bash
make check
```

Validation must cover:

- lint and formatting;
- dead-code;
- mypy;
- duplicate-code detection;
- unit and integration tests;
- required coverage.

Also verify that the critical HTTP routes remain present through OpenAPI or tests:

```text
GET /health
POST /payroll/import/spreadsheet
POST /payroll/import/json
POST /payroll/pdf-preview
GET /payroll
GET /payroll/{period_id}
GET /payroll/pension-plans
GET /payroll/health-plans
```

Finally, search for remaining references:

```bash
grep -R \
  "interfaces.cli\|import-payroll\|template-test\|period-detail\|plan-snapshots" \
  .
```

The only acceptable matches should be explicitly historical references or files being
removed in the same change.

## Conclusion

Complete CLI removal is safe for automated `pf` ecosystem flows.

The correct operation is to remove the CLI interface, not application logic:

- `ImportPayroll` remains for import APIs.
- `ProcessImportedPayrollPeriods` remains for the automatic pipeline.
- `ComputeIncomeTax` remains for import processing.
- `PreviewPdfImport` remains for `POST /payroll/pdf-preview`.
- `PayrollQueries` remains for HTTP queries.
- `ReferenceDataQueries` remains for HTTP reference data.
- Only genuinely orphaned wiring, such as `get_compute_income_tax_use_case`, may be
  removed after consumer and test verification.

The only functional capability intentionally abandoned is manual terminal access, because
that workflow is no longer used.
