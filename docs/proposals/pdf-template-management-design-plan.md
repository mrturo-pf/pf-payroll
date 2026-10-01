# PDF template management — implementation plan (living document)

> Mirrors the convention set by `pdf-import-action-plan.md` and
> `spreadsheet-export-design-plan.md`: the design brief and design recommendation stay
> frozen as originally written; this file gets updated with real progress, findings,
> and decisions, dated, as the feature is actually built.

## Overall status

| Stage (per the recommendation's Item 6) | Description | Status |
| --- | --- | --- |
| 0 | `pf-db`: `PAY_PDF_TEMPLATE`/`PAY_PDF_TEMPLATE_FIELD` tables + migration + seed | **Complete** |
| 1 | `pf-payroll` read path: DTOs, `TemplateReader`, DB-backed `select_template()` wiring | **Complete** |
| 2 | `pf-payroll` write path: `TemplateRepository`, CRUD routes, validation | **Complete** |
| 3 | Docs/Postman | **Complete** |

All stages were implemented in a single session (2026-10-01), continuing from a
previous session that had already completed Stage 0 (see "Picked up mid-session"
below) and started reading the codebase for Stages 1–2.

## Picked up mid-session

This session continued from partially-completed work: `pf-db`'s migration
(`0009_pdf_template_tables.py`), `db/01_schema.sql`, and `db/04_seed_real.sql` (with
the full `walmart-chile-v1` template, 20 fields including `CCAF_LOAN`) were already
written and committed to the plan before this session started. The one remaining
`pf-db`-side loose end — documenting the new tables in `pf-db/docs/tables.md` (table
count 18→20, payroll tables 13→15, ERD relationship lines for
`PAY_EMPLOYER → PAY_PDF_TEMPLATE → PAY_PDF_TEMPLATE_FIELD → PAY_CONCEPT`) — was
finished at the start of this session.

One correction from that earlier work is worth restating here since it was found and
fixed before this session, not during it: the first seed-data pass had accidentally
corrupted several Spanish accented-character regex classes (`[OÓ]`, `[IÍ]`) into
plain-ASCII `[OO]`/`[II]` while hand-typing the SQL `INSERT`s — caught and fixed by
diffing against the original JSON template's patterns before the migration was
considered done.

## What was built

### Application layer

- **`application/dto.py`**: added `PdfTemplateFieldDTO` (`id`, `pdf_label_pattern`,
  `concept_code`, `kind: PayrollConceptKind`, `confidence: float = 0.9`) and
  `PdfTemplateDTO` (`id`, `template_id`, `employer_id: int | None`, `employer_name`,
  `employer_match_pattern`, `version`, `is_active`, `fields: list[PdfTemplateFieldDTO]`).
  Both `id` fields are `None` only for a not-yet-persisted create request — always
  populated for anything read back from storage. Plain, JSON-serializable data, no
  compiled `re.Pattern` — the only shape allowed to cross the
  application/infrastructure boundary for templates.
- **`application/ports/template_repository.py`** (new file): two Protocols, not one —
  `TemplateReader` (`list_active_templates()` only, consumed by `PreviewPdfImport`) and
  the fuller `TemplateRepository` (list/get/create/update/deactivate, consumed by the
  `/payroll/templates*` routes). Interface segregation: `PreviewPdfImport` only ever
  needs the one read, so it depends on the narrower port, not the whole CRUD surface —
  mirrors the existing `EmployerPaymentRuleReader` / `ReferenceDataRepository` split.
  One concrete class (`SqlAlchemyTemplateRepository`) satisfies both, same pattern
  `SqlAlchemyReferenceDataRepository` already uses.
- **`application/use_cases/preview_pdf_import.py`**: `PreviewPdfImport` now takes a
  **required** `TemplateReader` (new second constructor argument, before the existing
  optional `EmployerPaymentRuleReader`) and fetches the active templates once per
  `execute()` call, passing them into `extractor.extract_preview()`. Required, not
  optional like the reference-data reader: templates are the entire point of the
  extractor, so making this optional would silently degrade the endpoint's core
  behavior instead of failing loudly at wiring time (no silent fallbacks, per
  `pf-payroll/AGENTS.md`).
- **`application/ports/pdf_extractors.py`**: `PdfPayrollExtractor.extract_preview()`
  gained a third parameter, `templates: list[PdfTemplateDTO]` — the port has no
  storage/database access of its own, so the caller must supply them.
- **`application/errors.py`**: added `PdfTemplateNotFoundError(PayrollNotFoundError)`.

### Infrastructure layer

- **`infrastructure/pdf_import/templates.py`**: `load_templates()` and
  `_load_template_file()` (JSON-from-disk) deleted outright, replaced by
  `compile_templates(dtos: list[PdfTemplateDTO]) -> list[Template]` — same compilation
  responsibility (raw pattern strings → `re.Pattern`), different input source. `Template`,
  `TemplateField`, `select_template()`, `_template_score()`, `match_field()` are
  **unchanged** — the matching algorithm itself was never in scope for this move.
- **`infrastructure/pdf_import/templates/` directory deleted** (was
  `walmart-chile/v1.json`, the only file in it) — templates now live exclusively in
  `pf-db`'s `PAY_PDF_TEMPLATE*` tables.
- **`infrastructure/pdf_import/extractor.py`**: `TemplatePdfPayrollExtractor` lost its
  `__init__(templates_dir)` / `self._templates` entirely — it is now fully stateless.
  `extract_preview()` gained the `templates` parameter, compiles them via
  `compile_templates()` once per call, and proceeds exactly as before.
- **`infrastructure/db/models/pdf_template.py`** (new file): `PdfTemplateModel`
  (`PAY_PDF_TEMPLATE`) and `PdfTemplateFieldModel` (`PAY_PDF_TEMPLATE_FIELD`), the
  latter's `kind` column reusing the exact same `PayrollConceptKind` `StrEnum` +
  `values_callable=enum_values` pattern `PayrollConceptModel.kind` already uses
  (`native_enum=False`, so this is a `VARCHAR` + `CHECK`, not a second shared Postgres
  enum type — reusing the Python class name is purely a type-consistency choice, not a
  schema-level coupling). `fields` relationship: `cascade="all, delete-orphan"`.
- **`infrastructure/db/repositories/template_repository.py`** (new file):
  `SqlAlchemyTemplateRepository`, implementing both Protocols above. `update_template()`
  mutates the existing row's `employer_*`/`version` columns directly and **reassigns**
  the `fields` relationship wholesale (`model.fields = _to_field_models(...)`) rather
  than hand-writing a `DELETE` — the `cascade="all, delete-orphan"` on the relationship
  does that for free in the same flush. `_commit_or_raise()` translates `IntegrityError`
  (duplicate `template_id`, or a field's `concept_code` not existing in `PAY_CONCEPT`)
  into `PayrollValidationError` — the one `try`/`except` this module needs, shared by
  both `create_template()` and `update_template()`.

### Interfaces layer

- **`interfaces/api/routes/pdf_templates.py`** (new file, new `APIRouter`): standard
  REST verbs — `POST`/`GET`/`GET {id}`/`PUT {id}`/`DELETE {id}` under `/payroll/templates*`
  — the first genuinely CRUD-shaped resource in this API, unlike the rest's
  `POST`-only, action-shaped mutations. Pydantic `field_validator`s compile-check every
  regex (`employer_match_pattern`, each field's `pdf_label_pattern`) before the request
  ever reaches the database — a `422`, not a crash the next time an unrelated PDF is
  previewed (closing the exact gap the design brief called out). `fields` requires
  `min_length=1` (a template with zero fields could never clear the 3-field
  `MIN_TEMPLATE_MATCH_SCORE` to even be selected — a reasonable addition beyond what
  the recommendation specified, not over-engineering: it would be a template nobody
  could ever use).
- **`interfaces/api/dependencies.py`**: added `get_template_repository()` (full CRUD,
  used by the new routes) and `get_template_reader()` (narrower, used by
  `PreviewPdfImport`) — same concrete `SqlAlchemyTemplateRepository` class behind both,
  resolved through two separate `Depends()` providers so each consumer's wiring only
  ever declares the port it actually needs. `get_preview_pdf_import_use_case()` updated
  to take the new required `template_reader` parameter.
- **`interfaces/api/main.py`**: `pdf_templates_router` mounted **before**
  `payroll_router`, same reasoning as `payroll_export_router` — `GET
  /payroll/{period_id}` has no int converter on its path parameter, so `GET
  /payroll/templates` would otherwise be swallowed as `period_id="templates"`. Comment
  updated to cover both routers' static paths in one place.
- **`interfaces/cli/main.py`**: `_template_test_async()` now opens a real (read-only) DB
  session via the existing `_open_session()` helper and builds a
  `SqlAlchemyTemplateRepository` to pass as `PreviewPdfImport`'s `template_reader` — see
  "Finding: the CLI's `template-test` command silently lost its 'no DB' property"
  below, this is the fix for that finding, not a pre-planned change.

## Findings not anticipated by the design recommendation

Documenting these here rather than silently working around them, per the project
convention established by `spreadsheet-export-design-plan.md`'s own "corrections"
section.

### Finding 1 — the CLI's `template-test` command silently lost its "no DB access" property

Neither the design brief nor the recommendation ever mention the CLI at all, despite
`docs/api.md` documenting `template-test <pdf>`'s headline feature as "no DB access at
all, unlike `POST /payroll/pdf-preview`" — a genuine, load-bearing property for local
template debugging without needing Docker/Postgres running. Once templates moved
exclusively into `pf-db`, that property became structurally impossible to keep:
`PreviewPdfImport` now has a *required* `TemplateReader`, and the only implementation of
that Protocol reads from the database. There is no third option that preserves
"zero DB access" (e.g. a bundled fallback fixture) without reintroducing exactly the
git-tracked-template duplication this whole feature exists to eliminate.

**Decision:** `_template_test_async()` now opens a session via the same
`_open_session()` helper every other DB-backed CLI command already uses, and
`docs/api.md`/`docs/payroll-workflow.md` were updated to say so plainly instead of
leaving the old "no DB" claim to rot. This is a deliberate, documented regression in
one specific convenience property, not an oversight — raised here so nobody mistakes
the updated CLI behavior for a bug report.

### Finding 2 — `_get_model()`'s `include_inactive` parameter was dead code (YAGNI)

While writing `SqlAlchemyTemplateRepository`, the private `_get_model()` helper
(shared by `update_template()` and `deactivate_template()`) was initially given the
same `include_inactive: bool` parameter as the public `get_template()`/`list_templates()`
methods, for symmetry. Caught before commit: **both actual call sites always pass
`include_inactive=True`** — you must be able to `PUT`/`DELETE` an already-deactivated
template (idempotent deactivation, and allowing a correction to a mis-deleted
template), so there was never a real caller for an active-only variant of this
*private* helper. Simplified `_get_model()` to drop the parameter entirely — found by
100%-coverage enforcement (`make test-cov` reported the `if not include_inactive:`
branch inside `_get_model()` as never executed), not by manual code review, which is
itself worth noting: the coverage gate caught a real (if minor) design smell, not just
a missing test.

### Finding 3 — `PayrollConceptKind` the DTO `Literal` and `PayrollConceptKind` the model `StrEnum` are different types with the same name

`application/dto.py` already defines `PayrollConceptKind = Literal["income",
"discount"]` (used by every DTO, including the new `PdfTemplateFieldDTO.kind`).
`infrastructure/db/models/reference_data.py` *separately* defines `class
PayrollConceptKind(StrEnum)` with members `INCOME`/`DISCOUNT` (used by
`PayrollConceptModel.kind`, and now `PdfTemplateFieldModel.kind` too). These are two
distinct, same-named types — mypy caught the mismatch immediately
(`PdfTemplateFieldDTO.kind` typed as the `Literal` alias, but
`PdfTemplateFieldModel.kind` resolves to the `StrEnum`) when `_to_dto()` first tried to
pass `field.kind` straight through. The established codebase convention (confirmed via
`grep` — `payroll_repository_queries.py`'s `kind=concept.kind.value` and
`reference_data_repository.py`'s equivalent) is: read direction uses `.value` to unwrap
the model's `StrEnum` member into the DTO's plain `str`; write direction (new to this
feature — nothing previously *wrote* a `PayrollConceptKind`-typed column from a DTO)
needs the mirror-image `PayrollConceptKind(field.kind)` to wrap the DTO's plain `str`
back into the model's `StrEnum` member before constructing a `PdfTemplateFieldModel`.
Both directions are implemented in `_to_dto()`/`_to_field_models()` respectively, each
with a docstring note pointing at the other as the matching half of the conversion, so
a future reader doesn't need to rediscover this asymmetry from scratch.

## Testing done this session

- `tests/unit/infrastructure/pdf_import/test_templates.py` (rewritten): file-based
  fixtures (`tmp_path`, `json.dumps`) replaced with `PdfTemplateDTO` literals passed
  straight to `compile_templates()`. The `walmart-chile-v1` regression tests (AFP
  commission mapping, payslip-variant concept coverage, `CCAF_LOAN`) now use a
  hardcoded Python-literal mirror of the real seeded template instead of reading the
  (now-deleted) JSON file — kept as a genuine DB-free *unit* test of the matching
  logic; a real-seed-backed integration test is a documented follow-up, not done this
  session (no local Postgres available in this sandbox).
- `tests/unit/infrastructure/pdf_import/test_extractor.py` (rewritten): same
  file→DTO-literal migration; every `extract_preview()` call updated to the new
  3-argument signature.
- `tests/unit/application/test_preview_pdf_import.py` (rewritten): added
  `FakeTemplateReader`; every `FakePdfPayrollExtractor.extract_preview()` and
  `PreviewPdfImport(...)` call site updated for the new required `template_reader`
  argument.
- `tests/unit/infrastructure/db/repositories/test_template_repository.py` (new, 13
  tests): `_to_dto()` mapping, `list_templates`/`list_active_templates`/`get_template`
  (found/not-found), `create_template` (commits + rereads; `IntegrityError` →
  `PayrollValidationError`, with `rollback` asserted), `update_template` (not-found;
  in-place field replacement), `deactivate_template` (not-found; flips `is_active`).
  Mock-session style, matching `test_complementary_insurance_repository.py`'s existing
  convention (`AsyncMock`/`MagicMock`, not a real DB) — note `mock_session.add` must be
  explicitly overridden to a plain `MagicMock()` wherever production code calls
  `session.add()` without `await`, or `AsyncMock`'s default async stub leaves an
  unawaited-coroutine warning that pytest's `unraisableexception` plugin turns into a
  hard failure (same gotcha the existing `test_assign_plans_to_period_adds_new_plans`
  test already works around — worth remembering next time a new repository test is
  added here).
- `tests/integration/api/test_pdf_templates.py` (new, 16 tests, `TestClient` +
  dependency-override `FakeTemplateRepository`, matching `test_payroll_export.py`'s
  convention): create (201, invalid-regex 422, empty-fields 422, duplicate 400),
  list (active-only default, `include_inactive=true`), get (found including inactive,
  404), update (replaces fields, 404, `PayrollError`→400), deactivate (flips
  `is_active`, still gettable afterwards, 404), and the route-collision regression test
  for the `main.py` registration-order gotcha.
- `tests/unit/interfaces/test_api_dependencies.py` (extended): direct tests for
  `get_template_repository()`/`get_template_reader()` (both were initially only
  exercised indirectly through dependency-override-based integration tests, which
  never actually executes a real provider's body — caught by the 100%-coverage gate);
  `get_preview_pdf_import_use_case_is_instantiable` updated for the new required
  argument.
- `tests/unit/interfaces/test_cli_main.py` (updated):
  `test_template_test_async_delegates_to_preview_use_case` now fakes `_open_session`
  (an `@asynccontextmanager` yielding a plain `object()`) and asserts
  `PreviewPdfImport` receives both the extractor and a real
  `SqlAlchemyTemplateRepository`, reflecting Finding 1's fix.

**Results:** 491 passed, 100% coverage (`make test-cov`), `ruff check`/`ruff format`
clean, `mypy src` clean (0 errors across 97 source files). No tests skipped, no Docker
required for anything in this session's scope — Stage 0's DB-level pieces are
unit/structurally verified through this session's mocked-session repository tests, not
a live Postgres; a live round-trip against the real seeded `walmart-chile-v1` row
(matching `spreadsheet-export-design-plan.md`'s own "Live verification" precedent) is a
documented follow-up, not done this session.

## Docs / Postman updates (same session, per the mandatory-tracking rule)

- **`pf-db/docs/tables.md`**: table counts (18→20 total, 13→15 payroll), new ERD line
  (`PAY_EMPLOYER → PAY_PDF_TEMPLATE → PAY_PDF_TEMPLATE_FIELD → PAY_CONCEPT`).
- **`pf-payroll/docs/api.md`**: rewrote the `POST /payroll/pdf-preview` row to describe
  templates as DB-backed, not file-backed; added a new "PDF templates" section with all
  5 `/payroll/templates*` endpoints (request bodies, validation behavior, status codes,
  the route-registration-order note); rewrote the `template-test` CLI row per Finding 1.
- **`pf-payroll/docs/payroll-workflow.md`**: updated the PDF-ingestion intro paragraph
  and the `template-test` usage note to match Finding 1.
- **`pf-payroll/docs/architectural-report.md`**: updated §5.1's prose and code snippet
  (`TemplatePdfPayrollExtractor`'s docstring/signature) to match the DB-backed reality,
  with pointers to this plan doc.
- **`postman/pf-ecosystem.postman_collection.json`**: new "Templates" folder under
  `pf-payroll`, sibling to "Periods" and "Reference data" — Create/List/Get
  one/Update/Deactivate, with example bodies for the `acme-chile-v1` placeholder
  template used throughout this doc. Verified valid JSON after editing (`python3 -m
  json.tool` round trip).

## Files touched (full list)

**New (`pf-payroll`):**
- `src/payroll/application/ports/template_repository.py`
- `src/payroll/infrastructure/db/models/pdf_template.py`
- `src/payroll/infrastructure/db/repositories/template_repository.py`
- `src/payroll/interfaces/api/routes/pdf_templates.py`
- `tests/unit/infrastructure/db/repositories/test_template_repository.py`
- `tests/integration/api/test_pdf_templates.py`
- `docs/proposals/pdf-template-management-design-plan.md` (this file)

**Modified (`pf-payroll`):**
- `src/payroll/application/dto.py` (added `PdfTemplateFieldDTO`, `PdfTemplateDTO`)
- `src/payroll/application/errors.py` (added `PdfTemplateNotFoundError`)
- `src/payroll/application/ports/pdf_extractors.py` (`extract_preview()` signature)
- `src/payroll/application/use_cases/preview_pdf_import.py` (required `template_reader`)
- `src/payroll/infrastructure/pdf_import/templates.py` (`load_templates()` →
  `compile_templates()`)
- `src/payroll/infrastructure/pdf_import/extractor.py` (stateless, new signature)
- `src/payroll/infrastructure/db/models/__init__.py` (exports)
- `src/payroll/interfaces/repositories.py` (exports)
- `src/payroll/interfaces/api/dependencies.py` (new providers, updated wiring)
- `src/payroll/interfaces/api/main.py` (mounted `pdf_templates_router`)
- `src/payroll/interfaces/cli/main.py` (Finding 1 fix)
- `tests/unit/infrastructure/pdf_import/test_templates.py` (rewritten)
- `tests/unit/infrastructure/pdf_import/test_extractor.py` (rewritten)
- `tests/unit/application/test_preview_pdf_import.py` (rewritten)
- `tests/unit/interfaces/test_api_dependencies.py` (extended)
- `tests/unit/interfaces/test_cli_main.py` (updated one test)
- `docs/api.md`, `docs/payroll-workflow.md`, `docs/architectural-report.md`

**Deleted (`pf-payroll`):**
- `src/payroll/infrastructure/pdf_import/templates/walmart-chile/v1.json` (and the
  now-empty `templates/` directory)

**Modified (`pf-db`, from the mid-session pickup — see "Picked up mid-session" above):**
- `alembic/versions/0009_pdf_template_tables.py` (new migration)
- `db/01_schema.sql`, `db/04_seed_real.sql`
- `docs/tables.md`

**Modified (root `pf-base`):**
- `postman/pf-ecosystem.postman_collection.json`

## Change log

- **2026-10-01** — Stages 1–2 (application/infrastructure/interfaces read+write path)
  implemented and tested in this session, continuing from Stage 0 (`pf-db` schema +
  seed) completed earlier. Three findings documented above: the CLI's `template-test`
  command structurally lost its "no DB access" property (fixed, documented, not
  silently patched over); a dead `include_inactive` branch in a private repository
  helper caught by the 100%-coverage gate (simplified per YAGNI); a same-named but
  distinct `PayrollConceptKind` `Literal` (DTO layer) vs. `StrEnum` (model layer)
  required an explicit `.value`/wrap-in-enum conversion in both read and write
  directions. 491 tests passing, 100% coverage, ruff/mypy both clean. Docs (`api.md`,
  `payroll-workflow.md`, `architectural-report.md`) and the root Postman collection
  updated in the same session.
