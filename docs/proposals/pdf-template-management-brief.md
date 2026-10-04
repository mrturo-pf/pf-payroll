## Context

`pf-payroll` already resolves PDF payslip labels (`raw_label`) to `concept_code`s via a
template mechanism, exercised through `POST /payroll/pdf-preview`. Today, per
`infrastructure/pdf_import/templates.py`'s own docstring:

```python
"""Payroll PDF templates: employer-specific label -> concept_code mapping.

Templates are plain JSON files versioned in git under `templates/<employer
slug>/v<N>.json` -- never in the database (see docs/proposals -- this is
config, curated by a human per employer/format, not application data).
"""
```

Concretely, one template (`infrastructure/pdf_import/templates/walmart-chile/v1.json`)
exists today, shaped like:

```json
{
  "template_id": "walmart-chile-v1",
  "employer_name": "Corporative Chile",
  "version": 1,
  "employer_match": { "name_pattern": "(?i)corporative" },
  "fields": [
    {
      "pdf_label_pattern": "(?i)^CCAF\\s+.*VIGENTE$",
      "concept_code": "CCAF_LOAN",
      "kind": "discount",
      "confidence": 0.9
    }
  ]
}
```

`load_templates()` walks the directory recursively at request time (`templates.py` +
`extractor.py`: `TemplatePdfPayrollExtractor()` is instantiated fresh per-request inside
`dependencies.py`'s `Depends()` wiring, not cached at app startup), compiling every
`re.Pattern` on each call. `select_template()` picks the best-scoring template whose
`employer_match` matches the raw PDF text, and `match_field()` resolves each row's label
against that template's `fields`. This already happens on every `pdf-preview` request
today -- moving the source of truth elsewhere does not introduce a new per-request I/O
cost that doesn't already exist.

**I want to re-evaluate the "never in the database" decision above, and separately add
a way to manage templates without editing files in the repo at all.** Two related but
distinct changes:

1. **Storage: move templates out of git-tracked JSON files** into either (a) `pf-db`
   (a new table, following the `pf-payroll` -> `pf-db` migration-coordination rule the
   rest of this codebase already follows) or (b) a cloud file store (e.g. a GCS bucket).
   I don't have a strong opinion on which -- evaluate both and recommend one.
2. **Management: expose create/modify/delete endpoints** for templates, so a human (or a
   future admin tool) can add support for a new employer/payslip format without a code
   change + PR + deploy cycle. **Deletion must be logical (soft), never a hard `DELETE`
   row/object removal.**

## Important constraints

- **No GCS/Cloud Storage usage exists anywhere in this ecosystem today** (confirmed:
  zero hits for `google-cloud-storage`/`storage.googleapis`/`gcs://` across `pf-payroll`,
  `pf-rates`, `pf-sheets`). Introducing one would be genuinely new infrastructure: a new
  bucket, a new service account + IAM grant, a new dependency
  (`google-cloud-storage` or similar), new Secret Manager wiring for credentials, and a
  new class of runtime failure (bucket unreachable) that doesn't exist today. Weigh this
  explicitly against `pf-db`, which is already the single source of truth for every
  other piece of reference/application data pf-payroll touches, is already provisioned
  (external Neon Postgres, chosen specifically to avoid Cloud SQL cost -- see root
  `AGENTS.md`'s cost-optimization rule), and already has a migration/seed pipeline this
  feature could reuse as-is.
- **Cost first, always** (root `AGENTS.md`, non-negotiable). Whichever storage you
  recommend, state its cost impact explicitly and name the cheaper alternatives
  considered -- this is a hurdle every prior proposal in this folder had to clear.
- **If the recommendation is `pf-db`:** this is schema-owning territory. Per root
  `AGENTS.md`, `pf-payroll` "never edits ORM models without a corresponding migration in
  pf-db" -- a new table needs a real Alembic migration in `pf-db` (hand-written SQL, a
  real `downgrade()`, idempotent) plus an ORM model + repository method in `pf-payroll`.
  This is a heavier cross-repo dependency than, e.g., the spreadsheet-export feature
  (which was read-only against existing tables) -- call out the coordination explicitly
  in your action plan, it is not optional.
- **Logical delete has an existing precedent in this exact schema -- follow it, don't
  invent a new convention.** `PAY_PENS_INST` and `PAY_HLTH_INST`
  (`pf-db/db/01_schema.sql`) both already use a plain
  `is_active BOOLEAN NOT NULL DEFAULT TRUE` column, flipped by an `UPDATE`, never a row
  delete. Prefer that shape over a `deleted_at TIMESTAMPTZ` unless you have a concrete
  reason `is_active` doesn't fit (e.g. needing to know *when* something was
  deactivated) -- justify either way.
- **Regex correctness at write time.** `employer_match.name_pattern` and every field's
  `pdf_label_pattern` are compiled via `re.compile()` at load time today; an invalid
  pattern currently would only ever surface as a crash the next time a PDF is previewed.
  A create/update endpoint must validate every pattern compiles *before* accepting the
  write (400, not a runtime surprise on the next unrelated `pdf-preview` call from a
  different employer entirely).
- **`PAY_EMPLOYER` already exists as a real table** (`pf-db/db/01_schema.sql`, `id`
  BIGSERIAL, `name` VARCHAR UNIQUE) -- decide whether a template row should carry a real
  `employer_id` FK (stronger integrity, but the existing `employer_match.name_pattern`
  regex exists precisely because the *printed* name on a PDF doesn't necessarily match
  `PAY_EMPLOYER.name` verbatim) versus keeping the current freeform regex-only matching,
  versus both (FK for administration/filtering, regex still used for the actual PDF-text
  match). Don't drop the regex-matching capability without an equivalent replacement --
  it's the mechanism that makes `select_template()` work at all.
- **Versioning is already a first-class concept** (`template_id`, `version: int`,
  directory-per-employer allowing multiple versions to coexist, `select_template()`
  using `version` as a tiebreaker). Decide and justify: does "modify" mutate a template
  row in place, or does it always create a new version (immutable history)? Both are
  defensible -- state the tradeoff (audit trail / rollback safety vs. simplicity) and
  pick one.
- **Existing hexagonal architecture applies unchanged:** `interfaces -> application ->
  domain`, `infrastructure -> application`. `domain/` has no I/O. Ports are
  `typing.Protocol` (there is currently no port for template storage at all --
  `templates.py` is called directly from `extractor.py`; introducing a
  `TemplateRepository` port is likely necessary regardless of which backend you choose,
  since `extractor.py` currently reads the filesystem directly with no seam to swap
  it out). `Decimal` is not relevant here (no financial amounts in a template), but
  every other rule applies: no `assert` for production validation, no silent fallbacks,
  DRY (don't fork the existing `Template`/`TemplateField` matching logic in
  `select_template()`/`match_field()` -- those should keep working unchanged regardless
  of where the raw template data comes from).
- **Backward compatibility for the one template that exists today.** Whatever migration
  path you propose must account for `walmart-chile/v1.json` (the only template in
  production right now, recently extended with the `CCAF_LOAN` field) -- state how it
  gets seeded into the new storage (a one-time migration script, a `pf-db` seed entry,
  or a manual `POST` once the create endpoint exists) and whether the git-tracked JSON
  file gets deleted once that's done or kept temporarily as a fallback.
- **Ecosystem documentation rule (non-negotiable, from the root `AGENTS.md`):** any new
  endpoint requires `pf-payroll/docs/api.md` **and** the root
  `postman/pf-ecosystem.postman_collection.json` updated in the *same* change. Treat
  this as part of the deliverable, not a follow-up.

## What I need

1. **Storage backend recommendation: `pf-db` vs. cloud file storage.** Compare both on:
   cost (infra + ongoing), consistency with the rest of this codebase's architecture,
   operational complexity (who/how backs it up, how it's seeded for local dev via
   `make seed-base`/`seed-real` vs. a bucket's own tooling), and how each interacts with
   the existing git-versioned-file removal goal. Pick one and justify it.
2. **Schema/shape design.** If `pf-db`: propose the table(s) -- at minimum something
   covering today's `Template` (`template_id`, `employer_name` or `employer_id` FK,
   `version`, `employer_match` pattern, `is_active`) and `TemplateField`
   (`pdf_label_pattern`, `concept_code`, `kind`, `confidence`) shape, 1:N between them.
   State whether `fields` belongs in its own table (normalized, matching
   `TemplateField`'s current shape 1:1) or as a JSONB column on the template row
   (denormalized, closer to today's single-JSON-file shape) -- justify the choice. If
   cloud storage instead: propose the object layout/naming convention and what (if
   anything) still needs a `pf-db` row (e.g. just an index/pointer, or nothing at all).
3. **Port + adapter placement.** Propose a `TemplateRepository` (or similarly named)
   port as a `typing.Protocol` that `extractor.py` depends on instead of calling
   `load_templates()` directly, with a concrete adapter for whichever backend you
   recommend. State whether `select_template()`/`match_field()`/the `Template`/
   `TemplateField` dataclasses change at all, or whether they stay exactly as-is and
   only the *loading* mechanism is swapped (my expectation is the latter -- confirm or
   correct).
4. **CRUD endpoint design.** Method, exact path(s), and request/response shape for
   create, modify (update), and logical delete. Also decide: does this feature need a
   list/get-by-id endpoint too (there is currently *no* way to inspect templates via the
   API at all -- unlike the spreadsheet-export feature, this is genuinely new API
   surface, not read access to already-queryable data)? State your reasoning either way
   -- YAGNI applies, but "I can create and modify something I can never list" is worth
   weighing against that.
5. **Validation rules for writes.** At minimum: every regex must compile (see
   constraints above); `(employer, version)` or `template_id` uniqueness; what happens
   if two active templates would tie on `_template_score()` for the same PDF (should
   creation/modification reject an ambiguous new template, warn, or is this out of
   scope for this feature and left to the existing tiebreak-by-version logic)?
6. **Migration plan for the existing `walmart-chile-v1` template.** As described in the
   constraints above -- concrete steps, including whether/when the git-tracked JSON file
   is removed.
7. **Action plan.** Stages, smallest independently-shippable slice first (I'd guess:
   schema/migration -> read path swapped to the new backend, keeping the file as a
   fallback or seed source -> write endpoints -> remove the git-tracked file -- confirm
   or correct this ordering). Call out the `pf-db` coordination explicitly as its own
   stage/dependency, and the `docs/api.md` + Postman update as part of the stage that
   introduces the new endpoints, not an afterthought.

## Response format

- **Deliverable:** a new Markdown file (not an inline reply, not a code PR), placed in
  the same folder as this document (`docs/proposals/`).
- Ground every claim in the actual code (`templates.py`, `extractor.py`,
  `dependencies.py`, `pf-db/db/01_schema.sql`) the way prior recommendations in this
  folder have -- cite file/line-level evidence, don't assume.
- Include a worked example: show what the migrated `walmart-chile-v1` template looks
  like in your recommended storage shape (a `pf-db` row/rows, or a bucket object), and
  a sample request/response body for each of the three CRUD endpoints (create, modify,
  logical delete).
- End with your recommendation (storage backend + endpoint shapes) and why.

## Stack

- Language: Python (FastAPI, hexagonal, already established in pf-payroll).
- Storage candidates to evaluate: PostgreSQL via `pf-db` (new table(s), real migration
  required) vs. a cloud object store (e.g. GCS -- no existing precedent in this
  ecosystem, would be new infrastructure).
- Expected volume: today, exactly one template, one employer. No high-throughput
  concern -- this is closer to the volume of `PAY_PENS_INST`/`PAY_HLTH_INST` (a handful
  of curated rows) than to payroll transaction data.
