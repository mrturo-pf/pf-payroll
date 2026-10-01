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
| 4 | Follow-up: denormalization fix (`kind`/`employer_name`), `pf-db` migration `0010` | **Complete** |

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

## Follow-up session (2026-10-01) — denormalization fix

After the above shipped and was deployed, a user code review of the merged schema
raised two direct questions: why does `PAY_PDF_TEMPLATE_FIELD` store `kind` when
`concept_code` is already a FK into `PAY_CONCEPT`, which already has its own `kind`?
And why does `PAY_PDF_TEMPLATE` store `employer_name` alongside a nullable
`employer_id → PAY_EMPLOYER`? Investigating confirmed both were genuine, unintentional
duplication introduced in the original design (not caught during the first
implementation or its review) — this section documents the findings and the fix,
implemented and shipped in the same follow-up session (`pf-db` migration `0010`).

### Finding — `PAY_PDF_TEMPLATE_FIELD.kind` duplicated `PAY_CONCEPT.kind` with no integrity link

`concept_code` was already `NOT NULL REFERENCES "PAY_CONCEPT"(code)`, and `PAY_CONCEPT`
already had its own `kind` column with the identical `income`/`discount` CHECK. Nothing
in the schema or the application layer ever cross-checked that a field's own `kind`
agreed with its `concept_code`'s real kind in `PAY_CONCEPT` — a client could `POST`/`PUT`
a field whose `kind` contradicted its `concept_code` (e.g. `concept_code: "INCOME_TAX"`,
`kind: "income"`) and it would be accepted verbatim, silently misclassifying that row as
income vs. discount at every future PDF preview. This was not a hypothetical: nothing in
`_commit_or_raise()`'s IntegrityError translation, nor any Pydantic validator, could have
caught it -- the two columns simply had no relationship to each other at the database
level.

**Fix:** dropped `PAY_PDF_TEMPLATE_FIELD.kind` outright (no legitimate case for the two
to differ -- unlike the employer case below, a concept's kind is not something a single
template should ever be allowed to override). `kind` is now always resolved from
`PAY_CONCEPT` via `concept_code`:
- **Read path:** `SqlAlchemyTemplateRepository._to_dto()` batch-resolves every distinct
  `concept_code` across the templates being mapped (`_dto_lookup_dicts()` /
  `resolve_concept_kinds()`, one query regardless of how many templates/fields --
  avoids N+1), and looks up each field's real kind by code. `PdfTemplateFieldDTO.kind`
  keeps its existing shape (`PayrollConceptKind` Literal) -- only its *source* changed,
  not its type, so `compile_templates()`/`select_template()`/`match_field()` in
  `infrastructure/pdf_import/templates.py` needed zero changes.
- **Write path:** `TemplateFieldRequest` (the Pydantic request schema) no longer has a
  `kind` field at all -- a client cannot send one, so there is nothing to silently
  ignore or contradict. `interfaces/api/routes/pdf_templates.py`'s new
  `_resolve_field_dtos()` helper resolves every field's kind from
  `TemplateRepository.resolve_concept_kinds()` *before* constructing the `PdfTemplateDTO`
  passed to `create_template()`/`update_template()`, and raises `PayrollValidationError`
  (-> 400) for any `concept_code` with no matching `PAY_CONCEPT` row -- earlier and
  clearer than the previous behavior of waiting for the FK constraint to fail during
  commit, with no functional regression (still a 400 either way).

### Finding — `PAY_PDF_TEMPLATE.employer_name` duplicated `PAY_EMPLOYER.name` in every case seen

Unlike `kind`, this one has a legitimate reason the two values could differ in
principle: `employer_id` is nullable (a template is not required to be linked to a
`PAY_EMPLOYER` row), and even when linked, the name printed on a real PDF is not
guaranteed to match `PAY_EMPLOYER.name` verbatim. But in practice, the one real seeded
template (`walmart-chile-v1`) had `employer_name = 'WALMART-CHILE'` set to the exact
same string as the `PAY_EMPLOYER` row its own `employer_id` already pointed to --
confirmed by direct query, not assumption. So the column was real duplication for the
only data that existed, even though the schema couldn't rule out a legitimate future
divergence.

**Fix (narrower than the `kind` fix, by design):** `employer_name` was made nullable
rather than dropped, with a new `chk_pay_pdf_template_employer_ref` CHECK
(`employer_id IS NOT NULL OR employer_name IS NOT NULL`) guaranteeing every row can
still resolve *some* display name. No value is ever copied from `PAY_EMPLOYER` into this
column anymore:
- **Read path:** mirrors the `kind` fix's shape -- `_to_dto()` resolves a `NULL`
  `employer_name` via a batched `_load_employer_names()` lookup keyed by `employer_id`,
  shared with `resolve_concept_kinds()` through the same `_dto_lookup_dicts()` call.
  `PdfTemplateDTO.employer_name` is typed `str | None` following the exact same
  None-only-on-write precedent the dataclass's docstring already established for `id`
  (`None` only for a not-yet-persisted write request; always resolved for anything read
  back from storage).
- **Write path:** `TemplateWriteRequest.employer_name` became `str | None = None`, with
  a new `model_validator(mode="after")` (`_require_employer_name_or_id`) mirroring the
  DB CHECK -- a request with neither `employer_name` nor `employer_id` is a `422`
  before ever reaching the repository.
- **One real mypy consequence:** `infrastructure/pdf_import/templates.py`'s
  `compile_templates()` builds the matching-engine's own `Template.employer_name: str`
  (non-Optional -- matching code should never have to think about a missing employer
  name) from `PdfTemplateDTO.employer_name: str | None`. Split out a `_compile_template()`
  helper that raises `PayrollValidationError` (marked `# pragma: no cover`, matching the
  existing precedent at `create_template`'s own post-commit re-read check) if `None`
  ever reaches it -- defensive only, since every DTO reaching `compile_templates()` came
  from a `TemplateReader` read, where the repository always resolves it.

### Why `kind` was dropped outright but `employer_name` was only made nullable

Worth stating explicitly since the two fixes look asymmetric: `concept_code` is
`NOT NULL`, so resolving `kind` from it is *always* possible -- there is no case where a
field can exist without a resolvable kind, so keeping a (redundant, unenforceable)
`kind` column added pure risk for zero benefit. `employer_id` is nullable by original,
deliberate design (see migration `0009`'s own docstring: "a template is not required to
be linked to a `PAY_EMPLOYER` row"), so a literal `employer_name` genuinely has to
remain available as a fallback for that case, and as an override for the
printed-name-differs-from-canonical-name case -- dropping it outright would have been a
real functional regression, not just a cleanup.

### Validation done this session

- Added `test_to_dto_resolves_employer_name_from_dict_when_column_is_null`,
  `test_to_dto_leaves_employer_name_none_when_unresolvable`,
  `test_get_template_resolves_employer_name_when_null`,
  `test_resolve_concept_kinds_maps_code_to_value`,
  `test_resolve_concept_kinds_empty_codes_short_circuits` to
  `test_template_repository.py`; `test_create_template_rejects_unknown_concept_code`,
  `test_create_template_rejects_missing_employer_name_and_id` to
  `test_pdf_templates.py`. 498 total tests, 100% coverage, ruff/mypy/vulture/jscpd
  clean on `pf-payroll`; `ruff check alembic/` clean on `pf-db`.
- **Live Postgres round-trip** (the follow-up explicitly deferred in the prior session,
  now done): spun up `pf-db`'s local stack (`make db-up`), applied the fixed
  `01_schema.sql` fresh, ran `seed-base`+`seed-real`, and confirmed by direct query that
  `walmart-chile-v1` now has `employer_id=9, employer_name=NULL` and all 20 fields
  correctly join to `PAY_CONCEPT.kind`. Separately simulated migration `0010`'s
  `upgrade()`/`downgrade()` SQL bodies directly against that seeded data (bypassing a
  broken local `pf-db/.venv` -- its interpreter symlink pointed at a path from before
  the `pf-db` → `pf/modules/pf-db` repo reorg, unrelated to this change, not fixed here
  since recreating it hung on a slow/incompatible dependency resolution for Python
  3.14 and was out of scope): `downgrade()` correctly backfilled `employer_name` back
  to `'WALMART-CHILE'` and `kind` back onto all 20 rows before restoring both `NOT
  NULL`s, and re-running `upgrade()` afterward cleanly reproduced the fixed shape again
  -- a genuine, data-preserving round trip, not just a lint pass.

### Files touched (follow-up session)

**New (`pf-db`):**
- `alembic/versions/0010_pdf_template_denormalization_fix.py`

**Modified (`pf-db`):**
- `db/01_schema.sql` (nullable `employer_name` + CHECK; dropped `kind` column)
- `db/04_seed_real.sql` (`employer_name` now `NULL`; field `INSERT` no longer sets `kind`)
- `docs/tables.md` (both tables' docs updated to match)

**Modified (`pf-payroll`):**
- `src/payroll/infrastructure/db/models/pdf_template.py` (nullable `employer_name` +
  `CheckConstraint`; dropped `kind` column/enum imports)
- `src/payroll/application/dto.py` (`PdfTemplateDTO.employer_name: str | None`)
- `src/payroll/application/ports/template_repository.py` (added
  `resolve_concept_kinds()` to the `TemplateRepository` Protocol)
- `src/payroll/infrastructure/db/repositories/template_repository.py` (rewritten
  resolution logic: `_load_employer_names()`, `_dto_lookup_dicts()`,
  `resolve_concept_kinds()`, updated `_to_dto()`/`_to_field_models()`)
- `src/payroll/interfaces/api/routes/pdf_templates.py` (removed `kind` from
  `TemplateFieldRequest`; `employer_name` optional + `model_validator`;
  `_resolve_field_dtos()` replacing `_to_field_dtos()`)
- `src/payroll/infrastructure/pdf_import/templates.py` (`_compile_template()` split out
  for mypy narrowing)
- `tests/unit/infrastructure/db/repositories/test_template_repository.py` (rewritten
  for the new lookup-dict-based `_to_dto()` signature; new tests, see above)
- `tests/integration/api/test_pdf_templates.py` (removed `kind` from fixtures; added
  `resolve_concept_kinds()` to both fakes; new tests, see above)
- `docs/api.md` (`POST /payroll/templates` row rewritten)

**Modified (`pf-base`):**
- `postman/pf-ecosystem.postman_collection.json` (removed `kind` from both Templates
  create/update example bodies; updated both requests' descriptions)

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
- **2026-10-01 (second follow-up)** -- `PAY_PDF_TEMPLATE_FIELD.concept_code` replaced
  with `concept_id` (migration `0011`), fixing the one FK into `PAY_CONCEPT` in the
  whole schema that didn't use its surrogate `id` like every sibling table does. Full
  writeup, live Alembic upgrade/downgrade/upgrade round trip against real Postgres, and
  file list in the "Second follow-up" section below. 500 tests passing, 100% coverage,
  ruff/format/mypy/vulture/jscpd all clean. Zero `api.md`/Postman changes needed -- the
  client-facing `concept_code` contract never changed, confirming the fix landed
  exactly at the repository boundary.

## Second follow-up: `PAY_PDF_TEMPLATE_FIELD.concept_code` → `concept_id`

A second round of review (prompted by the same instinct that caught the `kind`/
`employer_name` duplication: "does this column actually need to exist, or does it
just copy data another table already owns?") turned up one more schema
inconsistency, orthogonal to the first one.

### Finding: `concept_code` was the only FK into `PAY_CONCEPT` using the business key

Every other table that needs to point at a `PAY_CONCEPT` row does so through the
surrogate integer primary key -- `PAY_ITEM.concept_id BIGINT REFERENCES
PAY_CONCEPT(id)` is the established, and only other, example in the schema.
`PAY_PDF_TEMPLATE_FIELD.concept_code`, added in migration `0009`, was a
one-off: a `VARCHAR(40)` FK into `PAY_CONCEPT(code)` (the business key) instead.
Nothing about PDF templates specifically needed that difference -- it looks like an
oversight from treating `concept_code` as "the identifier" at the application
layer (which it correctly is) without noticing that every sibling table still
stores the surrogate `id` underneath. This is purely a storage-layer
inconsistency, not an application-facing one: `concept_code` was, and remains,
the only identifier any DTO, request, response, or the PDF-matching engine
(`CONCEPT_MAP`) ever sees.

### Decision: storage changes to `concept_id`; the API contract does not change at all

Unlike the `kind`/`employer_name` fix, this one required **zero** changes to
`docs/api.md` or the Postman collection -- confirmation that the fix belongs
exactly at the repository boundary and nowhere else. Clients still send and
receive `concept_code` in every request/response body; only the column
`PdfTemplateFieldModel` maps to, and what `PAY_PDF_TEMPLATE_FIELD` actually
stores, changed from `concept_code` to `concept_id`.

### pf-db implementation

- `alembic/versions/0011_pdf_template_field_concept_id.py`, revising `0010`:
  adds nullable `concept_id`, backfills it with `UPDATE ... FROM PAY_CONCEPT`
  joining on the old `concept_code`, sets `NOT NULL` + the new FK
  (`fk_pay_pdf_template_field_concept_id` → `PAY_CONCEPT(id)`), drops
  `concept_code`, and adds `idx_pay_pdf_template_field_concept_id`. `downgrade()`
  reverses the same join in the other direction (code ← id) before restoring the
  old `NOT NULL REFERENCES PAY_CONCEPT(code)` shape -- no data loss either way,
  exactly like migration `0010`'s round trip.
- `db/01_schema.sql`: `PAY_PDF_TEMPLATE_FIELD.concept_id BIGINT NOT NULL REFERENCES
  PAY_CONCEPT(id)` replaces `concept_code`; added the matching index.
- `db/04_seed_real.sql`: the `VALUES` literal still spells out human-readable
  codes (`'SALARY_BASE'`, etc. -- there's no reason to hand-maintain raw
  integers in a seed file humans read), but the `INSERT` now joins that literal
  against `PAY_CONCEPT` and writes `c.id`, not the code itself.
- `docs/tables.md`: `PAY_PDF_TEMPLATE_FIELD`'s schema block and prose updated to
  describe `concept_id`/the new index/the "every other table already does this"
  rationale.

### pf-payroll implementation

- `PdfTemplateFieldModel.concept_code: Mapped[str]` → `concept_id: Mapped[int]`
  (`ForeignKey("PAY_CONCEPT.id")`).
- `application/dto.py`: added `ConceptRef` (`id: int`, `kind: PayrollConceptKind`)
  -- the one DTO shaped specifically for the by-code concept lookup a write needs
  (both the concept's real id, to store, and its kind, for the response). DTOs
  that cross the `TemplateRepository` port boundary stayed otherwise unchanged;
  `PdfTemplateFieldDTO.concept_code`/`.kind` are still exactly what every
  route/test already expected.
- `application/ports/template_repository.py`: `resolve_concept_kinds(codes) ->
  dict[str, PayrollConceptKind]` renamed/widened to `resolve_concepts(codes) ->
  dict[str, ConceptRef]` -- one lookup now serves both the write path (needs
  `.id`) and what used to need a separate kind-only lookup, instead of two
  near-identical Protocol methods.
- `infrastructure/db/repositories/template_repository.py`:
  - `resolve_concepts()` queries `(code, id, kind)`, keyed by code -- the write
    path's lookup.
  - New private `_load_concepts_by_id()` queries `(id, code, kind)`, keyed by
    id -- the mirror-image lookup the *read* path (`_to_dto()`) needs, since a
    loaded `PdfTemplateFieldModel` only carries `concept_id`, not the code. Kept
    separate from the public `resolve_concepts()` (different key direction,
    different DTO shape needed) rather than overloading one method for both
    directions.
  - `_to_field_models()` (now an async instance method, since it needs the
    session to resolve codes) calls `resolve_concepts()` and raises
    `PayrollValidationError` on any code with no match -- unchanged
    400-on-unknown-code behavior, just re-homed from "trust the FK
    IntegrityError" to "reject explicitly before ever building the model", same
    as it already did for the `kind` fix.
  - `_dto_lookup_dicts()` now calls `_load_concepts_by_id()` instead of a
    code-keyed kind lookup; `_to_dto()`'s second parameter is
    `concepts_by_id: dict[int, tuple[str, str]]` (both code and kind, since a
    single `PAY_CONCEPT` row lookup resolves both at once for the read path).
  - Both lookups remain batched (one query per `list_templates()`/
    `get_template()` call, not per-row) -- the N+1 guard from the original
    recommendation is preserved unchanged.
- `interfaces/api/routes/pdf_templates.py`: `_resolve_field_dtos()` now calls
  `repository.resolve_concepts()` and reads `.kind` off the returned
  `ConceptRef` -- the only call-site change; request/response shapes and the
  400-on-unknown-code behavior are byte-for-byte identical to before.

### Live PostgreSQL validation (this session, not simulated)

With the `pf-db` `.venv` repaired (see the root `AGENTS.md`/kennel memory on the
`SSL_CERT_FILE`-unrelated `uv`/Artifactory proxy fix), this round trip was run
for real, through actual Alembic, not hand-run SQL:

1. `make db-reset` (fresh, empty Postgres) → `alembic upgrade head`: the full
   `0001`→`0011` chain applied cleanly in one run, including `0009`/`0010`/`0011`
   back-to-back against a database that had never seen any of them before.
2. `make seed-real`: all 20 `walmart-chile-v1` fields inserted successfully,
   joining the seed's literal codes to real `PAY_CONCEPT.id` values.
3. Direct query confirmed `\d "PAY_PDF_TEMPLATE_FIELD"` shows `concept_id
   BIGINT NOT NULL` with `fk_pay_pdf_template_field_concept_id` and the new
   index, no `concept_code` column at all; joining `concept_id` back to
   `PAY_CONCEPT` resolved all 20 codes/kinds correctly (e.g. `concept_id=1` →
   `SALARY_BASE`/`income`).
4. `alembic downgrade 0010`: `concept_code` column restored, correctly
   backfilled for all 20 rows from the live `concept_id` values (verified by
   query) -- no data loss.
5. `alembic upgrade head` again: `concept_id` restored, all 20 rows re-verified
   against `PAY_CONCEPT` a second time -- a genuine two-way round trip on a
   real, previously-migrated-forward-and-back database, not a fresh load each
   time.

### Tests and quality checks

- Rewrote `tests/unit/infrastructure/db/repositories/test_template_repository.py`
  end to end: every mocked `session.execute()` call sequence had to be split out
  by the exact row shape each of `resolve_concepts()` (code, id, kind) vs.
  `_load_concepts_by_id()` (id, code, kind) actually selects -- reusing one
  `MagicMock` `return_value` across calls with conflicting tuple-unpack orders
  would have silently produced wrong dicts instead of a visible failure (mocks
  don't type-check tuple unpacking). Added
  `test_resolve_concepts_maps_code_to_ref`,
  `test_resolve_concepts_empty_codes_short_circuits`,
  `test_load_concepts_by_id_empty_ids_short_circuits`,
  `test_to_field_models_rejects_unknown_concept_code`.
- `tests/integration/api/test_pdf_templates.py`: both fakes'
  `resolve_concept_kinds()` renamed to `resolve_concepts()`, now returning
  `ConceptRef` instances instead of bare kind strings. No other test needed to
  change -- confirms the API contract really is untouched by this fix.
- Full suite: **500 tests passing, 100% coverage**; `ruff check`/`ruff format
  --check`/`mypy`/`vulture`/`jscpd` (`make duplicate-code-src`) all clean on
  `pf-payroll`; `ruff check alembic/` clean on `pf-db` (via the now-repaired
  `.venv`).

### Files touched (second follow-up session)

**New (`pf-db`):**
- `alembic/versions/0011_pdf_template_field_concept_id.py`

**Modified (`pf-db`):**
- `db/01_schema.sql` (`concept_code` → `concept_id` + new index)
- `db/04_seed_real.sql` (`INSERT` now joins seed literals to `PAY_CONCEPT.id`)
- `docs/tables.md` (`PAY_PDF_TEMPLATE_FIELD` section updated)

**Modified (`pf-payroll`):**
- `src/payroll/infrastructure/db/models/pdf_template.py` (`concept_code` →
  `concept_id` column)
- `src/payroll/application/dto.py` (new `ConceptRef` dataclass)
- `src/payroll/application/ports/template_repository.py`
  (`resolve_concept_kinds()` → `resolve_concepts()`)
- `src/payroll/infrastructure/db/repositories/template_repository.py` (new
  `_load_concepts_by_id()`; `resolve_concepts()` returns `ConceptRef`;
  `_to_field_models()`/`_to_dto()`/`_dto_lookup_dicts()` updated for the new
  key direction)
- `src/payroll/interfaces/api/routes/pdf_templates.py` (`_resolve_field_dtos()`
  updated call-site only)
- `tests/unit/infrastructure/db/repositories/test_template_repository.py`
  (rewritten; see above)
- `tests/integration/api/test_pdf_templates.py` (both fakes updated; see above)

No `docs/api.md` or Postman changes -- confirmed the API contract is untouched by
this fix.

## Third follow-up: drop `PAY_PDF_TEMPLATE.employer_name` outright

The user explicitly reversed the second follow-up session's default recommendation
("keep `employer_name` nullable as a legitimate onboarding fallback"): the column
should not exist at all. Rather than assume a resolution, this was clarified directly
-- `employer_id` was already nullable specifically because there is no standalone
endpoint to create a `PAY_EMPLOYER` row ahead of a template (one is only ever created
as a side effect of a full payroll import), so dropping `employer_name` without a
decision on `employer_id` would silently reintroduce the exact chicken-and-egg problem
that justified keeping it nullable in the first place. Asked directly: **`employer_id`
becomes `NOT NULL`/required** -- a real, confirmed behavior change, not an oversight:
it is no longer possible to pre-create a template for a brand-new employer before
their first import has run.

### A nice side effect: `employer_name` simplifies from `str | None` to effectively
### always-`str` on every read

Once `employer_id` is guaranteed `NOT NULL` with a real FK into `PAY_EMPLOYER`, every
loaded template's employer is guaranteed to exist -- `employer_name` no longer needs a
conditional "resolve only if the column itself was NULL" branch in `_to_dto()`; it is
unconditionally looked up for every row's `employer_id`. `PdfTemplateDTO.employer_id`
itself also simplifies from `int | None` to plain `int` (still `str | None` for
`employer_name` at the dataclass level only because of the pre-existing `id: int |
None` write/read split precedent -- `None` on a not-yet-persisted create/update DTO,
always resolved to a real `str` on anything read back from storage).

### pf-db implementation

- `alembic/versions/0012_pdf_template_employer_id_required.py`, revising `0011`:
  defensively backfills any (theoretical -- none exist in the real seed)
  `employer_id IS NULL` row by joining its old `employer_name` to `PAY_EMPLOYER.name`,
  then raises a `RAISE EXCEPTION` inside a `DO $$` block if any row still can't resolve
  one (no silent partial migration), before setting `employer_id NOT NULL`, dropping
  `chk_pay_pdf_template_employer_ref`, and dropping `employer_name`. `downgrade()`
  restores the nullable column (as `NULL` -- the original literal text is gone for
  good, which is fine: it was never anything but a display fallback) and the CHECK.
- `db/01_schema.sql`/`db/04_seed_real.sql`/`docs/tables.md` updated to match --
  `04_seed_real.sql`'s `INSERT`/`ON CONFLICT` clauses no longer mention `employer_name`
  at all.

### pf-payroll implementation

- `PdfTemplateModel.employer_id` is now `Mapped[int]` (no longer nullable); the
  `employer_name` column and its `CheckConstraint` are gone entirely.
- `PdfTemplateDTO.employer_id: int` (was `int | None`); `employer_name` keeps its
  `str | None` write/read-split typing.
- `interfaces/api/routes/pdf_templates.py`: `TemplateWriteRequest` drops
  `employer_name` entirely and makes `employer_id: int` required (no default) --
  `_require_employer_name_or_id` (the whole reason `model_validator` was imported)
  is deleted outright, since there's nothing left to cross-validate. `TemplateRead.
  employer_id` is now plain `int`.
- `infrastructure/db/repositories/template_repository.py`: new
  `_validate_employer_exists()` mirrors the unknown-`concept_code` pattern exactly --
  rejects an unknown `employer_id` with a clear `PayrollValidationError` (-> 400)
  *before* `create_template()`/`update_template()` ever call `self._session.add()`/
  mutate the model, rather than relying solely on the FK `IntegrityError` translated
  by `_commit_or_raise()` (kept as a safety net regardless -- now realistically only
  ever fires for a genuine duplicate `template_id`). `_dto_lookup_dicts()`'s
  `employer_ids` computation simplifies from a filtered set ("only ids where
  `employer_name is None`") to unconditionally every `employer_id` in the batch, since
  every row now needs one. `_to_dto()` indexes `employer_names[model.employer_id]`
  directly (no more `.get()`/`None` fallback) -- a `KeyError` here would mean a real
  data-integrity break worth surfacing loudly, not masking.

### Live PostgreSQL validation

Same `make db-reset` -> `alembic upgrade head` (full `0001`->`0012` chain, one run) ->
`make seed-real` -> direct-query -> `alembic downgrade 0011` -> direct-query ->
`alembic upgrade head` -> direct-query round trip as the prior two follow-ups,
confirming: `employer_id BIGINT NOT NULL` with its FK, no `employer_name` column, no
CHECK constraint, `walmart-chile-v1` correctly joins to `PAY_EMPLOYER` (`employer_id=3`
-> `WALMART-CHILE`) after `upgrade head`; `downgrade 0011` correctly restores a
nullable `employer_name` column (as `NULL`, per design) without losing `employer_id`;
re-`upgrade head` cleanly reproduces the fixed shape again with the same `employer_id`
intact throughout.

### Tests and quality checks

- Unit tests: removed the three tests specific to the old "resolve from dict only if
  column is NULL" branch (`test_to_dto_resolves_employer_name_from_dict_when_column_
  is_null`, `test_to_dto_leaves_employer_name_none_when_unresolvable`,
  `test_get_template_resolves_employer_name_when_null` -- that branch no longer
  exists); added `test_load_employer_names_empty_ids_short_circuits`,
  `test_validate_employer_exists_rejects_unknown_id`,
  `test_create_template_rejects_unknown_employer_id_before_any_write` (net zero test
  count change, 500 total). Every mocked multi-`execute()`-call test
  (`create_template`/`update_template`) grew one more `side_effect` entry for the new
  `_validate_employer_exists()` lookup.
- Integration tests: `TemplateWriteRequest` no longer has `employer_name`, so every
  `PdfTemplateDTO(...)` fixture across the file needed a real `employer_id` (was
  freely `None` before); `test_create_template_rejects_missing_employer_name_and_id`
  renamed to `test_create_template_rejects_missing_employer_id` (now just asserts the
  plain-required-field 422, no cross-field validator left to exercise).
- Full suite: **500 tests passing, 100% coverage**; `ruff check`/`ruff format
  --check`/`mypy`/`vulture`/`jscpd` all clean on `pf-payroll`; `ruff check alembic/`
  clean on `pf-db`.

### `docs/api.md`/Postman changes (unlike the second follow-up, this one has real ones)

Unlike the `concept_id` fix, this *is* a client-visible contract change -- updated:

- `docs/api.md`'s `POST /payroll/templates` row: `employer_id` now documented as
  required; `employer_name` removed from the request description entirely; added the
  new "`employer_id` that does not exist" 400 case.
- `postman/pf-ecosystem.postman_collection.json`: both the Create and Update Template
  example request bodies replace `"employer_name": "ACME-CHILE"` with `"employer_id":
  1`; the Create request's description rewritten to match.

### Files touched (third follow-up session)

**New (`pf-db`):**
- `alembic/versions/0012_pdf_template_employer_id_required.py`

**Modified (`pf-db`):**
- `db/01_schema.sql`, `db/04_seed_real.sql`, `docs/tables.md`

**Modified (`pf-payroll`):**
- `src/payroll/infrastructure/db/models/pdf_template.py` (`employer_id` required;
  `employer_name` column + `CheckConstraint` removed)
- `src/payroll/application/dto.py` (`PdfTemplateDTO.employer_id: int`)
- `src/payroll/interfaces/api/routes/pdf_templates.py` (`employer_name` request field
  + `_require_employer_name_or_id` validator removed; `employer_id` required)
- `src/payroll/infrastructure/db/repositories/template_repository.py` (new
  `_validate_employer_exists()`; `_to_dto()`/`_dto_lookup_dicts()` simplified)
- `tests/unit/infrastructure/db/repositories/test_template_repository.py` (rewritten;
  see above)
- `tests/integration/api/test_pdf_templates.py` (every fixture updated; see above)
- `docs/api.md` (`POST /payroll/templates` row rewritten)

**Modified (root `pf-base`):**
- `postman/pf-ecosystem.postman_collection.json` (`employer_name` -> `employer_id` in
  both example bodies + the Create description)

