# Response: PDF Template Management Design

Response to `pdf-template-management-brief.md`. Grounded in the actual code
(`templates.py`, `extractor.py`, `preview_pdf_import.py`, `dependencies.py`,
`pf-db/db/01_schema.sql`, `test_templates.py`) rather than assumptions.

## Storage backend: `pf-db` — confirmed, not close

**Recommendation: a new table pair in `pf-db`, not cloud file storage.**

- **Zero new infrastructure.** Confirmed: `google-cloud-storage`/`storage.googleapis`/
  `gcs://` have zero hits anywhere in `pf-payroll`, `pf-rates`, or `pf-sheets`. A bucket
  means a new dependency, a new service account + IAM grant, a new Secret Manager entry
  for credentials, and a new class of runtime failure (bucket unreachable) — none of
  which exist today. A `pf-db` table needs none of that: the migration tooling, the
  async SQLAlchemy session, the seed pipeline (`02_seed_base.sql`/`04_seed_real.sql`),
  and the production Neon connection are already wired end to end.
- **No per-request I/O regression.** `TemplatePdfPayrollExtractor()` is already
  instantiated fresh per request (`dependencies.py`:
  `return PreviewPdfImport(TemplatePdfPayrollExtractor(), reference_data)`, called from
  a `Depends()` with no caching), and `load_templates()` already does synchronous
  filesystem I/O + `re.compile()` on every single `/payroll/pdf-preview` call today. A
  DB read replacing a file read at the same call site is not a new cost class, just a
  different one — likely cheaper in practice (one indexed query vs. a recursive
  directory walk).
- **Volume is trivial.** One template, one employer, today. This sits at
  `PAY_PENS_INST`/`PAY_HLTH_INST` scale (a handful of curated rows), not payroll
  transaction scale.
- **Cost: effectively $0 marginal.** No new GCP resource, no new line item. The only
  new cost is one migration + a few KB of rows in a database already being paid for.

## Schema design: two normalized tables

```sql
-- pf-db/db/01_schema.sql (new, via alembic/versions/0009_pdf_template_tables.py)

CREATE TABLE IF NOT EXISTS "PAY_PDF_TEMPLATE" (
    id                     BIGSERIAL     PRIMARY KEY,
    template_id            VARCHAR(80)   NOT NULL UNIQUE,
    employer_id            BIGINT        REFERENCES "PAY_EMPLOYER"(id),
    employer_name          VARCHAR(120)  NOT NULL,
    employer_match_pattern VARCHAR(500)  NOT NULL,
    version                INTEGER       NOT NULL DEFAULT 1 CHECK (version > 0),
    is_active              BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at             TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS "PAY_PDF_TEMPLATE_FIELD" (
    id                BIGSERIAL     PRIMARY KEY,
    template_id       BIGINT        NOT NULL REFERENCES "PAY_PDF_TEMPLATE"(id) ON DELETE CASCADE,
    pdf_label_pattern VARCHAR(500)  NOT NULL,
    concept_code      VARCHAR(40)   NOT NULL REFERENCES "PAY_CONCEPT"(code),
    kind              VARCHAR(20)   NOT NULL CHECK (kind IN ('income', 'discount')),
    confidence        NUMERIC(3,2)  NOT NULL DEFAULT 0.90 CHECK (confidence BETWEEN 0 AND 1)
);
```

**Why normalized (two tables), not a JSONB `fields` column:**

- **`concept_code` gets a real FK to `PAY_CONCEPT(code)`.** This is a genuine
  integrity upgrade the current git-JSON format cannot offer at all: adding `CCAF_LOAN`
  to `walmart-chile-v1.json` two sessions ago required a human to manually cross-check
  it against `pf-db/db/02_seed_base.sql`'s seed — a typo'd `concept_code` there would
  have silently produced a field that never resolves to a real concept, only
  discoverable at `pdf-preview` time against a real PDF. A FK makes that class of bug
  impossible to persist in the first place.
- **`PAY_CONCEPT.code` is `VARCHAR(40) NOT NULL UNIQUE`** (confirmed,
  `01_schema.sql`), which is a valid FK target even though it isn't the primary key —
  same shape already used for other natural-key references in this schema.
- **Matches this schema's own established style.** `PAY_ITEM` → `PAY_CONCEPT`,
  `PAY_PENS_PLAN` → `PAY_PENS_INST` are already normalized 1:N with FKs, not JSONB
  blobs. A `fields` JSONB column would be the only denormalized reference-data shape in
  this entire schema.
- **Field-level updates stay simple.** Fixing one field's regex (exactly what happened
  with `CCAF_LOAN`) becomes a one-row `UPDATE`, not a full-array read-modify-write of a
  JSON blob with no per-element identity.

**`employer_id` is a nullable, admin-only FK — not used by matching.** `PAY_EMPLOYER`
already exists (`id` BIGSERIAL, `name` VARCHAR UNIQUE), but the printed employer name on
a real PDF doesn't necessarily match `PAY_EMPLOYER.name` verbatim — that's exactly why
`employer_match_pattern` (a regex against the full raw PDF text) exists and must keep
being the operative matching mechanism, unchanged. `employer_id` is included purely so a
future "list templates for employer X" admin view doesn't have to re-derive that
relationship from a regex; `select_template()`/`match_field()` never read it.

**`is_active BOOLEAN DEFAULT TRUE`** follows the exact precedent in `PAY_PENS_INST` /
`PAY_HLTH_INST` (`01_schema.sql` lines 114, 123) rather than a `deleted_at` timestamp —
no concrete need surfaced for knowing *when* a template was deactivated, so the simpler,
already-established shape wins.

## Port + adapter placement

**Key finding that shapes this whole section:** `PdfPayrollExtractor.extract_preview()`
is a **synchronous** method (`application/ports/pdf_extractors.py`:
`def extract_preview(...)`, no `async`), while `PreviewPdfImport.execute()` (the use
case wrapping it) **is** `async def` and already conditionally awaits a second read-only
port (`EmployerPaymentRuleReader`) before/after calling the extractor. This is the exact
seam needed — no new architectural pattern has to be invented, just reused:

```python
# application/dto.py — new DTOs (plain data, JSON-serializable, no compiled regex —
# this is what's allowed to cross the application/infrastructure boundary)

@dataclass(frozen=True, slots=True)
class PdfTemplateFieldDTO:
    id: int | None  # None for not-yet-persisted (create requests)
    pdf_label_pattern: str
    concept_code: str
    kind: PayrollConceptKind
    confidence: float

@dataclass(frozen=True, slots=True)
class PdfTemplateDTO:
    id: int | None
    template_id: str
    employer_id: int | None
    employer_name: str
    employer_match_pattern: str
    version: int
    is_active: bool
    fields: list[PdfTemplateFieldDTO]
```

```python
# application/ports/template_reader.py — narrow, read-only, mirrors
# EmployerPaymentRuleReader's own "one method, one consumer" interface-segregation

class TemplateReader(Protocol):
    async def list_active_templates(self) -> list[PdfTemplateDTO]:
        """List every active (is_active=True) template, fields included."""
        ...
```

```python
# infrastructure/pdf_import/templates.py — Template/TemplateField (compiled regex)
# stay exactly as they are today; only their *source* changes.

def compile_templates(dtos: list[PdfTemplateDTO]) -> list[Template]:
    """Compile a list of PdfTemplateDTO into matchable Template objects.

    Direct replacement for load_templates() + _load_template_file() -- same
    compilation responsibility (raw pattern strings -> re.Pattern), different
    input source (DTOs from a DB read, not JSON files from disk).
    """
    return [
        Template(
            template_id=dto.template_id,
            employer_name=dto.employer_name,
            version=dto.version,
            employer_match=re.compile(dto.employer_match_pattern),
            fields=tuple(
                TemplateField(
                    pattern=re.compile(f.pdf_label_pattern),
                    concept_code=f.concept_code,
                    kind=f.kind,
                    confidence=f.confidence,
                )
                for f in dto.fields
            ),
        )
        for dto in dtos
    ]
```

```python
# application/ports/pdf_extractors.py — port signature grows one parameter

class PdfPayrollExtractor(Protocol):
    def extract_preview(
        self, filename: str, content: bytes, templates: list[PdfTemplateDTO]
    ) -> PdfImportPreviewDTO: ...
```

`TemplatePdfPayrollExtractor.__init__()` loses `templates_dir` and its `load_templates()`
call entirely — it becomes a stateless class. `_extract_preview()` gains one line,
`compiled = compile_templates(templates)`, then uses `compiled` exactly where it used
`self._templates` before. **`select_template()`, `match_field()`, `_template_score()`,
`Template`, and `TemplateField` do not change at all** — confirming the brief's item 3
guess: only the loading mechanism moves, the matching logic is untouched.

```python
# application/use_cases/preview_pdf_import.py

class PreviewPdfImport:
    def __init__(
        self,
        extractor: PdfPayrollExtractor,
        template_reader: TemplateReader,                       # now required
        reference_data: EmployerPaymentRuleReader | None = None,
    ) -> None: ...

    async def execute(self, filename: str, content: bytes) -> PdfImportPreviewDTO:
        if not filename:
            raise PayrollValidationError("A PDF file name is required.")
        templates = await self._template_reader.list_active_templates()
        preview = self._extractor.extract_preview(filename, content, templates)
        return await self._with_real_payment_date(preview)
```

**`template_reader` is required, unlike `reference_data`.** `EmployerPaymentRuleReader`
is a genuine nice-to-have (falls back to a generic payment-date guess when absent);
templates are the entire point of the extractor — without them every row comes back
unresolved. Making it optional would silently degrade the endpoint's core behavior
instead of failing loudly at wiring time, which is exactly the "no silent fallbacks"
rule this codebase already holds itself to elsewhere.

`dependencies.py` wiring:

```python
def get_preview_pdf_import_use_case(
    reference_data: EmployerPaymentRuleReader = Depends(get_reference_data_repository),
    template_reader: TemplateReader = Depends(get_template_reader),
) -> PreviewPdfImport:
    return PreviewPdfImport(TemplatePdfPayrollExtractor(), template_reader, reference_data)
```

`get_template_reader()` follows the same pattern as `get_reference_data_repository()`:
a plain (non-transactional) async session-backed repository, since this is read-only.

## CRUD endpoint design

**Verb precedent check (confirmed by reading every route in `payroll.py`,
`reference_data.py`, `payroll_export.py`): this API has zero `PUT`/`PATCH`/`DELETE`
routes today.** Every mutation so far is `POST` (`/{period_id}/review`,
`/{period_id}/compute-tax`, `/import/json`, ...), but every one of those is
**action-shaped** (an RPC-style verb on an existing period), not **resource-shaped**.
Templates are the first genuinely CRUD-shaped resource this API manages — standard REST
verbs are the right idiom here, not a reason to force everything through `POST` for
consistency with a different kind of endpoint.

```
POST   /payroll/templates                    -- create
GET    /payroll/templates                    -- list (active only by default)
GET    /payroll/templates/{template_id}      -- get one (any status)
PUT    /payroll/templates/{template_id}      -- modify (full replace)
DELETE /payroll/templates/{template_id}      -- logical delete (is_active -> false)
```

`{template_id}` is the external string id (`"walmart-chile-v1"`), not the internal
numeric `id` — consistent with how `template_id` already appears externally today in
`PdfImportPreviewResponse.template_id`.

**`GET /payroll/templates` needs a list endpoint even though the brief flagged this as
an open YAGNI question.** Justification: unlike every other feature in this codebase,
this is genuinely new write-capable API surface with no existing way to inspect state —
"you can create and modify a template but never see what exists" would make the
create/modify endpoints nearly unusable operationally (a caller would have to remember
every `template_id` it ever created). `?include_inactive=true` (default `false`) covers
the soft-deleted case without a second endpoint.

**Request/response models:**

```python
class TemplateFieldRequest(BaseModel):
    pdf_label_pattern: str
    concept_code: str
    kind: Literal["income", "discount"]
    confidence: float = 0.9

    @field_validator("pdf_label_pattern")
    @classmethod
    def _pattern_must_compile(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"Invalid regex: {exc}") from exc
        return value

class TemplateCreateRequest(BaseModel):
    template_id: str
    employer_name: str
    employer_match_pattern: str  # same _pattern_must_compile validator
    version: int = 1
    employer_id: int | None = None
    fields: list[TemplateFieldRequest]

class TemplateUpdateRequest(BaseModel):
    employer_name: str
    employer_match_pattern: str
    version: int
    employer_id: int | None = None
    fields: list[TemplateFieldRequest]

class TemplateFieldRead(BaseModel):
    id: int
    pdf_label_pattern: str
    concept_code: str
    kind: str
    confidence: float

class TemplateRead(BaseModel):
    id: int
    template_id: str
    employer_id: int | None
    employer_name: str
    employer_match_pattern: str
    version: int
    is_active: bool
    fields: list[TemplateFieldRead]
```

`DELETE` returns `200` with the deactivated `TemplateRead` body (`is_active: false`),
matching this API's existing preference for informative response bodies over bare `204`s
(`/{period_id}/review`, `/{period_id}/compute-tax` both return rich response models, not
empty responses).

## Validation rules for writes

- **Every regex must compile before being accepted** — a Pydantic `field_validator`
  (shown above) rejects with `422` at the request-shape level, before any DB write is
  attempted. This directly closes the gap called out in the brief: today an invalid
  pattern in a hand-edited JSON file would only surface as a crash the next time an
  unrelated PDF is previewed.
- **`concept_code` integrity is enforced twice, for two different failure modes:** the
  Pydantic layer cannot know whether a `concept_code` string is real (that requires a DB
  round-trip), so the `PAY_PDF_TEMPLATE_FIELD.concept_code` FK is the actual guarantee.
  A `POST`/`PUT` that references a nonexistent `concept_code` fails the FK constraint at
  write time; the repository/use case must catch that `IntegrityError` and translate it
  into a `PayrollValidationError` (`400`, via `to_http_exception()` — the existing
  pattern already used across this codebase) rather than leaking a raw DB error. This
  is a strictly better failure mode than today's file-based system, which cannot catch
  this at all until a real PDF hits it.
- **`template_id` uniqueness** is enforced by the existing `UNIQUE` constraint proposed
  above; `POST` with a duplicate `template_id` is the same `IntegrityError` ->
  `PayrollValidationError` translation.
- **Ambiguous-scoring ties between active templates are explicitly out of scope for this
  feature**, per the brief's own framing — `select_template()`'s existing
  version-number tiebreak already handles the one concrete tie case in production
  (`TestSelectTemplate.test_picks_highest_scoring_candidate`); this feature does not
  change matching behavior, only where templates come from, so introducing new
  ambiguity-rejection logic here would be scope creep (YAGNI) unless a real incident
  demonstrates the need.

## Migration plan for `walmart-chile-v1`

1. **`pf-db` migration `0009`** creates both tables (see schema above), plus an
   `INSERT` into `04_seed_real.sql` — the same file that already seeds the one real
   `WALMART-CHILE` row into `PAY_EMPLOYER` — carrying the current, live contents of
   `walmart-chile/v1.json` (all fields, including the recently added `CCAF_LOAN` one) as
   real seed data. This keeps local dev / any fresh environment bootstrapped with the
   real template via `make seed-real`, with no manual `POST` required to reach parity
   with today's file-based state.
2. **Switch the read path** (`TemplateReader` + `compile_templates()` per the port
   section above) in the same pull request that lands the migration's consumer code —
   not held open as a "temporary fallback" alongside the file. This codebase has already
   hit real bugs from exactly this kind of dual-source drift (a stale `pf-db` seed
   resurrecting an already-corrected row, documented in this same `docs/proposals/`
   folder's `spreadsheet-export` brief) — a template read path that can silently pick
   either source is the same risk shape, not a safety net.
3. **Delete `infrastructure/pdf_import/templates/walmart-chile/v1.json`** (and the
   `templates/` directory itself once empty) plus `load_templates()` /
   `_load_template_file()` from `templates.py`, in that same change.
4. **Update `test_templates.py`.** Every regression test in this file currently calls
   `_load_real_shipped_template()`, which calls `load_templates()` with no arguments
   (reading the real file from disk) — `TestAfpCommissionRegression`,
   `TestPayslipVariantConceptCoverage`, and `TestCcafLoanConceptCoverage` all depend on
   this. Once the file is deleted, this helper must instead build the same template via
   `compile_templates()` fed a hardcoded `PdfTemplateDTO` fixture holding the exact
   payload the file used to hold (a Python literal replacing the JSON literal — same
   data, same regression coverage, zero DB dependency for these **unit** tests, which
   should stay DB-free). The real DB-backed path (the actual `TemplateReader` SQL query)
   gets its own, separate **integration** test against the seeded `04_seed_real.sql` row
   from step 1 — this is a different concern (does the SQL query work) from what
   `test_templates.py` already covers (does the matching logic work).

## Action plan

**Stage 0 — `pf-db`:** migration `0009` (both tables), `04_seed_real.sql` INSERT for
`walmart-chile-v1` + its fields, `docs/tables.md` update. No `pf-payroll` code depends on
this yet — independently mergeable and deployable first.

**Stage 1 — `pf-payroll` read path:** `PdfTemplateDTO`/`PdfTemplateFieldDTO` in
`application/dto.py`; `TemplateReader` port; `compile_templates()` in `templates.py`;
`extract_preview()` signature change; `PreviewPdfImport` takes a required
`TemplateReader`; `SqlAlchemyTemplateRepository` (read side) in
`infrastructure/db/repositories/`; `dependencies.py` wiring. Delete the git-tracked JSON
file + `load_templates()`/`_load_template_file()`; migrate `test_templates.py`'s fixture
per the migration plan above. This stage alone makes `/payroll/pdf-preview` fully
DB-backed with **zero new endpoints** — verifiable by re-running the exact same
`secrets/liquidacion/*.PDF` regression check done in the CCAF_LOAN session and confirming
identical `concept_code` resolution before/after cutover.

**Stage 2 — `pf-payroll` write path:** the fuller `TemplateRepository` port (create,
update, deactivate, get, list — implemented by the same `SqlAlchemyTemplateRepository`
class satisfying both `TemplateReader` and `TemplateRepository`, mirroring how
`SqlAlchemyReferenceDataRepository` already satisfies both `EmployerPaymentRuleReader`
and the fuller `ReferenceDataRepository` today); the five routes under
`/payroll/templates`; the regex-compile validators; the `IntegrityError` ->
`PayrollValidationError` translation. `docs/api.md` and the Postman collection updated
in this same stage, not after.

**Stage 3 — verification:** the `test_templates.py` migration itself (folded into Stage
1, but worth calling out as its own checklist item since it touches every existing
regression test in that file), plus new integration tests for the five CRUD endpoints
(create -> list shows it -> get by id -> update changes a field -> delete sets
`is_active=false` -> a subsequent `pdf-preview` no longer matches it).

**Cross-repo coordination:** real, and heavier than the read-only `spreadsheet-export`
feature — Stage 0 must merge and deploy in `pf-db` before Stage 1 can land in
`pf-payroll` (the migration must exist before the new repository code can query the
tables it creates). No coordination needed with `pf-rates` or `pf-sheets`.

## Worked example

**Migrated `walmart-chile-v1` as DB rows** (Stage 0's `04_seed_real.sql` INSERT,
abbreviated to 2 of its fields for space):

```
PAY_PDF_TEMPLATE
 id | template_id      | employer_id | employer_name  | employer_match_pattern | version | is_active
----+------------------+-------------+----------------+------------------------+---------+-----------
  1 | walmart-chile-v1 |        <id> | Walmart Chile  | (?i)walmart            |       1 | true

PAY_PDF_TEMPLATE_FIELD
 id | template_id | pdf_label_pattern          | concept_code | kind     | confidence
----+-------------+----------------------------+--------------+----------+-----------
  1 |           1 | (?i)^CCAF\s+.*VIGENTE$     | CCAF_LOAN    | discount |       0.90
  2 |           1 | (?i)COMISIÓN AFP           | PENSION_ADDITIONAL | discount | 0.90
```

**`POST /payroll/templates`** (creating a brand-new employer's template):

```json
{
  "template_id": "acme-v1",
  "employer_name": "ACME",
  "employer_match_pattern": "(?i)acme",
  "version": 1,
  "fields": [
    {
      "pdf_label_pattern": "(?i)^SUELDO$",
      "concept_code": "SALARY_BASE",
      "kind": "income",
      "confidence": 0.9
    }
  ]
}
```

Response `201`:

```json
{
  "id": 2,
  "template_id": "acme-v1",
  "employer_id": null,
  "employer_name": "ACME",
  "employer_match_pattern": "(?i)acme",
  "version": 1,
  "is_active": true,
  "fields": [
    {
      "id": 3,
      "pdf_label_pattern": "(?i)^SUELDO$",
      "concept_code": "SALARY_BASE",
      "kind": "income",
      "confidence": 0.9
    }
  ]
}
```

**`PUT /payroll/templates/acme-v1`** (fixing a typo'd pattern in place, no version
bump — mirrors exactly what happened with `CCAF_LOAN`'s pattern this session):

```json
{
  "employer_name": "ACME",
  "employer_match_pattern": "(?i)acme",
  "version": 1,
  "fields": [
    {
      "pdf_label_pattern": "(?i)^SUELDO\\s*BASE$",
      "concept_code": "SALARY_BASE",
      "kind": "income",
      "confidence": 0.9
    }
  ]
}
```

**`DELETE /payroll/templates/acme-v1`** — response `200`:

```json
{
  "id": 2,
  "template_id": "acme-v1",
  "employer_id": null,
  "employer_name": "ACME",
  "employer_match_pattern": "(?i)acme",
  "version": 1,
  "is_active": false,
  "fields": [ ... ]
}
```

A subsequent `list_active_templates()` call (and therefore `/payroll/pdf-preview`)
stops matching `acme-v1` immediately — the row still exists, only `is_active` flipped.

## Recommendation

Build in the three stages above: `pf-db` migration + real seed data first (independent,
ship it alone), then the DB-backed read path with zero new endpoints (verifiable against
real payslips before touching anything else), then the five `/payroll/templates` CRUD
routes with proper REST verbs (`POST`/`GET`/`PUT`/`DELETE` — a deliberate first for this
API, justified by this being its first genuinely resource-shaped feature) and logical
delete via the existing `is_active` convention. The only genuinely new piece of domain
modeling this surfaced is the `PAY_PDF_TEMPLATE_FIELD.concept_code` FK to `PAY_CONCEPT` —
small, mechanical, and it closes a real integrity gap this exact codebase already felt
the cost of once (the manual `CCAF_LOAN` cross-check two sessions ago).

## Cost impact

Effectively none. No new GCP resource, no new dependency, no new IAM surface — two small
tables in the database already paid for (external Neon Postgres), holding on the order
of tens of rows. The only real cost is engineering time across the three stages, and the
one-time cross-repo coordination of Stage 0 landing before Stage 1.
