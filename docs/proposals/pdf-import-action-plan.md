# PDF import — action plan (living document)

> **Rule for this document:** unlike `pdf-import-design-brief.md` and
> `pdf-import-design-recommendation.md` (which are the original request and analysis,
> and stay frozen as they were written), **this file gets updated every work session**
> on the feature. Any progress, new finding, decision made, or scope change goes here,
> dated, before a stage is considered closed. If you find this document out of date
> relative to the code, update it yourself before continuing.

## Overall status

| Stage | Description | Status |
| --- | --- | --- |
| 0 | Period semantics (code + historical data) | **Complete** — code, local data, Neon (fixed by the user) and the source CSV are all aligned now |
| 1 | Endpoint 1 — PDF preview (MVP) | **Complete** — template-based extractor, real WALMART-CHILE template, `POST /payroll/import/pdf-preview` route |
| 2 | Endpoint 2 — confirm, `commit` mode | **Complete** — `ImportPayroll.from_rows()`, `POST /payroll/import/rows` route |
| 3 | Endpoint 2 — `validate` mode (rollback) | **Complete** — `TransactionalSessionScope` with a real SAVEPOINT |

## Stage 0 — Period semantics

### What was done (2026-09-25)

1. **Code:** `SqlAlchemyPayrollImportRepository._validate_payment_month_matches_period()`
   in `infrastructure/db/repositories/payroll_repository_imports.py`. It's called from
   `import_rows()` right after resolving/creating the `EmployerModel`, before touching
   `PAY_PERIOD`. It compares `payment_date`'s month/year against
   `add_months(date(period_year, period_month, 1), employer.payment_month_offset)` and
   raises `PayrollValidationError` if they don't match. With `payment_month_offset=0`
   (the default for every new employer), this requires `payment_date` to fall in the
   same month as `period_month` — the new convention, exactly as the PDF states it.
   - Test added: `test_sa_payroll_repository_rejects_payment_date_period_mismatch`
     in `tests/unit/infrastructure/test_payroll_repository.py` (covers the `raise`
     branch, needed to keep `--cov-fail-under=100`).
   - Coverage verified: 100% (286 tests, `pytest --cov=payroll --cov-report=term-missing`).
   - Lint (`make lint`) and typecheck (`make typecheck`) verified, no findings.
   - **Broke no existing test** — every current fixture/test already assumed
     `payment_date` and `period_month` in the same month (offset 0), so the new
     validation passed cleanly without touching any pre-existing import tests.

2. **Data (local environment only):** the local DB showed that **`WALMART-CHILE`**
   (`PAY_EMPLOYER.id = 9` locally, `payment_month_offset = 0`) is the only employer with
   imported periods (22 rows, Dec-2024 through Sep-2026), and **100%** of them followed
   the old convention (`period_month` = `payment_date`'s month + 1). The other two
   local employers (`DALT-CONSULTORES`, `CLINICA-ALEMANA`) have no periods loaded, so
   there was nothing to fix there.

   A one-off SQL script (idempotent, no PII — only `employer_id`/dates, never a
   worker's national ID (RUT)/name since `PAY_PERIOD` doesn't store those) was used to
   apply the fix. **At the user's explicit request, the file was removed from the repo**
   once local, Neon, and the source CSV were all aligned — it had already served its
   purpose and isn't kept under version control (if it ever needs to be rebuilt, the SQL
   was trivial: `UPDATE "PAY_PERIOD" SET period_year = EXTRACT(YEAR FROM payment_date),
   period_month = EXTRACT(MONTH FROM payment_date) WHERE employer_id = <WALMART-CHILE's
   id>;` followed by `REFRESH MATERIALIZED VIEW "PAY_MV_SUMARY"`).

   Applied and verified locally:
   ```
   UPDATE 22   -- WALMART-CHILE's 22 rows
   REFRESH MATERIALIZED VIEW "PAY_MV_SUMARY"
   ```
   Post-fix verification: each row's `period_year`/`period_month` now matches exactly
   its `payment_date`'s year/month (e.g. `payment_date=2024-11-28` now lives in the
   `2024-11` period, no longer in `2024-12`).

### Neon (2026-09-25)

The user fixed Neon directly (out of my scope, as agreed — I did not touch production).
Stage 0 is closed on the database side.

### Fixed data source (2026-09-25)

`secrets/payroll-input.csv` (the real CSV used to import WALMART-CHILE's history, kept
out of git due to real PII/financial data) had the same old convention across its 22
rows: `period_year`/`period_month` = `payment_date`'s month + 1. It was fixed by
recalculating `period_year`/`period_month` directly from `payment_date` with a one-off
Python script (not version-controlled, run only once — this is a gitignored data file,
not production code, so it didn't warrant a script under `scripts/data-fixes/` like the
DB one did). Verified: all 22 rows ended up with `period_year`/`period_month` equal to
their own `payment_date`'s year/month, consistent with the fix already applied locally
and in Neon. If this CSV is re-imported today, it passes cleanly through the new
validation described in "What was done" without triggering `PayrollValidationError`.

## Stage 1 — Endpoint 1 (PDF preview)

**Complete (2026-09-25).** No persistence: neither `PayrollRepository` nor
`ProcessImportedPayrollPeriods` are in this endpoint's dependency graph, by construction
(`PreviewPdfImport.__init__` only receives the `PdfPayrollExtractor` port).

### What was built

- **DTOs** (`application/dto.py`): `PdfImportPreviewRowDTO` (raw_label,
  extracted_amount_clp, kind, optional concept_code, confidence) and
  `PdfImportPreviewDTO` (employer/period/worked_days/declared_net_pay_clp, all
  optional, + template_id + rows). Everything optional on purpose: the contract is
  "never fail loudly", in the worst case an almost-empty preview is returned with a
  200 OK.
- **Port** (`application/ports/pdf_extractors.py`): `PdfPayrollExtractor.extract_preview()`.
- **Use case** (`application/use_cases/preview_pdf_import.py`): `PreviewPdfImport` —
  deliberately without a repository, it only delegates to the extractor.
- **New infrastructure** (`infrastructure/pdf_import/`):
  - `text_extraction.py`: generic, employer-agnostic helpers to read text from a PDF
    with `pypdf` (`extraction_mode="layout"`), parse the header (month/year in Spanish,
    worked days, net pay), and split the detail into `(label, amount)` lines, with a
    positional heuristic (HABERES vs. DESCUENTOS column) as a fallback signal when no
    template resolves a line.
  - `templates.py`: loading and matching of versioned JSON templates. Two-step
    selection: filter by `employer_match.name_pattern` against the full text, then
    score = number of `fields` whose pattern matches at least one detail label; minimum
    threshold `MIN_TEMPLATE_MATCH_SCORE = 3` (if not reached, nothing is assumed).
  - `extractor.py`: `TemplatePdfPayrollExtractor`, the port's implementation. Wraps
    everything in a `try/except Exception` — a corrupt PDF, a scan with no text layer,
    or any unexpected internal failure degrades to an empty preview instead of breaking
    the endpoint.
  - `templates/walmart-chile/v1.json`: **a real template**, built and validated against
    the user's real payslip (`secrets/Liquidación_202608.PDF`, never committed — only
    used locally to design/test the template). Maps WALMART-CHILE's 13 real concepts to
    their `concept_code` from `PAY_CONCEPT`, with the confidence already agreed on in
    `pdf-import-design-recommendation.md` (High=0.9, Medium-High=0.75, Medium=0.6).
    Verified end to end against the real PDF: employer, period (2026-8, already under
    the new convention), worked days (30), and net pay (3,133,182) — all 13 concepts
    resolve to a `concept_code`, zero unresolved rows.
- **Route** (`interfaces/api/routes/payroll.py`): `POST /payroll/import/pdf-preview`,
  wired in `interfaces/api/dependencies.py::get_preview_pdf_import_use_case` (no
  repository `Depends`, on purpose).

### Decisions/adjustments made during implementation

- The column heuristic (HABERES vs. DESCUENTOS) for rows not resolved by any template is
  computed **always** over the document's text (not only when there's no template at
  all), so it also acts as a fallback for a single unmatched row within a document whose
  template did match overall.
- A short token (3-6 characters) immediately before the amount is only treated as a
  "concept code" (and trimmed off the label) if it contains at least one digit — every
  real code observed mixes letters and numbers (`1E89`, `/370`, `3C30`). Without this, a
  short uppercase word at the end of a label with no real code (e.g. "LABEL") was being
  trimmed by mistake.
- Coverage: 100% across the 4 new files in `infrastructure/pdf_import/` + the use case +
  dependency/route wiring (337 total tests, full suite). Two genuinely unreachable
  defensive lines (given that the preceding regexes already guarantee the condition)
  were marked `# pragma: no cover` instead of forcing an artificial test.

### Pending / out of scope for this stage

- There is only a template for WALMART-CHILE. Any other employer/format currently falls
  into "no template" (preview with unresolved rows, but the header still parsed).
- No OCR, no LLM — scanned PDFs with no selectable text layer return an empty preview.
  Out of scope for the MVP, as stated in `pdf-import-design-recommendation.md`.

## Stage 2 — Endpoint 2, `commit` mode

**Complete (2026-09-25).** Reuses 100% of the existing pipeline — zero changes to
`SqlAlchemyPayrollImportRepository.import_rows()`, which was already agnostic of the
rows' source.

### What was built

- **Use case** (`application/use_cases/import_payroll.py`): `ImportPayroll.from_rows()`,
  a sibling method to `from_bytes()`. Skips the parsing step (`PayrollImporter`) and
  calls `self._repository.import_rows(rows)` directly. Rejects
  (`PayrollValidationError`) an empty list, just like `from_bytes()` rejects a file with
  no rows.
- **Request models** (`interfaces/api/routes/payroll.py`): `ImportPayrollRowRequest`
  (mirrors `ImportPayrollRowDTO`, without the output-only fields
  `expected_net_pay_clp`/`net_pay_difference_clp`) and `ImportPayrollRowsRequest`
  (`mode` + `rows`). `mode` is deliberately a `Literal["commit"]` — `"validate"` doesn't
  exist yet, so sending that value returns 422 from the schema, not from hand-written
  business logic (no dead branches waiting for Stage 3).
- **Route**: `POST /payroll/import/rows`, the exact same sequence as `/payroll/import`
  (`ImportPayroll.from_rows()` → `ProcessImportedPayrollPeriods.execute()`), reusing the
  already-existing `ImportPayrollResponse` response.

### How "reject unresolved concept_code" was solved (design section 3)

No extra defensive code needed: `ImportPayrollRowRequest.concept_code` is `str` (not
`str | None`), so a row with `concept_code: null` in the body never reaches the handler
— FastAPI/pydantic reject it with 422 before that. The type does the work where an `if`
would otherwise have been needed.

### Testing

- `tests/unit/application/test_import_payroll.py`: 2 new tests for `from_rows()`
  (delegates correctly to the fake repository / rejects an empty list).
- `tests/integration/api/test_payroll_import_rows.py` (new file): happy path (default
  and explicit mode), 422 in `validate` mode, 422 on a null `concept_code`, 400 on an
  empty list (propagated from the use case), 502 when `ProcessImportedPayrollPeriods`
  fails because of a downstream dependency being down (same pattern as the equivalent
  `/payroll/import` test).
- 345 total tests, 100% coverage (`--cov-fail-under=100`), lint/typecheck/vulture clean.

### Pending / out of scope for this stage

- No manual smoke test against a real running Postgres instance (there wasn't a local
  instance up during this session) — confidence comes from `import_rows()` not being
  touched (already 100% covered by `/payroll/import`) and `from_rows()` being a trivial
  passthrough, verified with fakes.
- The real transaction mechanism (`session.commit()` vs. `session.rollback()`) is left
  for Stage 3 — since `import_rows()` already does *multiple* internal `commit()`s per
  period (not a single one at the end), a correct `validate` needs to rethink the
  session's scope, not just add an `if mode == "commit"` at the end.

## Stage 3 — Endpoint 2, `validate` mode

**Complete (2026-09-25).** The finding documented when Stage 2 was closed turned out to
be correct: `import_rows()` (and several steps of `ProcessImportedPayrollPeriods`, see
`_refresh_summary_view()`/`_reconcile_period_net_pay()` in
`payroll_repository_shared.py`, and `payroll_repository_commands.py`) do **several**
internal `session.commit()`s during a single request. Wrapping the final call in a
`session.rollback()` wouldn't have undone any of that — each internal `commit()` had
already made its own durable transaction.

### How it was solved: a real SAVEPOINT, not a manual flag

Instead of touching the existing `session.commit()` calls scattered across half the
codebase (dozens of methods in `payroll_repository_commands.py`,
`payroll_repository_shared.py`, and several use cases), this uses SQLAlchemy 2.0's
native support for "joining" a session to an outer transaction with SAVEPOINT
semantics: `AsyncSession(bind=connection, join_transaction_mode="create_savepoint")`.
With this, every `session.commit()` the application code makes **only releases the
current SAVEPOINT** (SQLAlchemy automatically opens a new one) — the connection's real
transaction is never touched until someone explicitly calls `resolve()`. Zero changes
to existing code that was already calling `session.commit()` freely.

- **`interfaces/session.py`**: `TransactionalSessionScope` (wraps `session` +
  `_transaction`, exposes `resolve(mode)` which does `transaction.commit()` or
  `transaction.rollback()`) and `open_transactional_session()` (opens the connection,
  starts the real transaction, builds the session with
  `join_transaction_mode="create_savepoint"` and `expire_on_commit=False` — same as
  `SessionLocal` — and does a defensive rollback in the `finally` block if `resolve()`
  was never called).
- **`interfaces/api/dependencies.py`**: `get_transactional_session()` (FastAPI
  dependency, request-cached) + 4 sibling dependencies
  (`get_payroll_repository_for_rows_import`,
  `get_complementary_insurance_repository_for_rows_import`,
  `get_import_payroll_use_case_for_rows_import`,
  `get_process_imported_payroll_periods_use_case_for_rows_import`) that build the same
  Stage 2 use cases but bound to the transactional session instead of the usual "plain"
  session — needed because FastAPI caches dependencies per callable, there's no way to
  "parametrize" `get_session()` based on the body's `mode`.
- **Route**: now a single `try/except` around `from_rows()` +
  `ProcessImportedPayrollPeriods.execute()` (there used to be two, like in
  `/payroll/import`) — a deliberate divergence: any exception forces
  `scope.resolve("validate")` **before** re-raising the error, regardless of what `mode`
  the client asked for. A half-applied import can never end up committed.
- `ImportPayrollRowsRequest.mode` went from `Literal["commit"]` to
  `Literal["commit", "validate"]`.

### Testing — the first test against a real Postgres in all of pf-payroll

The entire existing suite uses fakes (`FakeSession`, `FakeResultsQueueBase`, etc.), and
that's still correct for 99% of the domain. But the SAVEPOINT mechanism makes a claim
about **real** transaction semantics that no fake can honestly verify: that several
internal `session.commit()`s really do vanish with `resolve("validate")`. For that:

- `tests/integration/infrastructure/test_transactional_session.py` (new): uses
  `testcontainers[postgres]` (an existing dev dependency, never used before in this
  repo) against a disposable `probe` table, without touching pf-db's schema. Three
  cases: `commit` persists both internal `commit()`s, `validate` discards both, and a
  never-resolved scope (bug/early-return) does a defensive rollback on its own. A
  `postgres:16-alpine` container is reused at the module level (already cached
  locally); a fresh async engine per test to avoid crossing asyncpg's connection pool
  between different pytest-asyncio event loops (`asyncio_mode = "strict"`, no shared
  loop).
  - Environment note: Ryuk (testcontainers' "reaper") fails to start on Rancher
    Desktop/macOS with a Docker socket mount error — a known issue, already solved in
    this monorepo: `pf-common/make/common.mk` detects Rancher's socket and
    automatically exports `TESTCONTAINERS_RYUK_DISABLED=true` for `make test`/
    `make test-cov`. Nothing needed to be touched there, just used.
- `tests/unit/interfaces/test_api_dependencies.py`: 5 new tests for the
  `_for_rows_import` dependencies + `get_transactional_session()`'s lifecycle (same
  pattern as the existing `assert_get_session_lifecycle`, now also
  `assert_get_transactional_session_lifecycle` in `tests/helpers/db_fakes.py`).
- `tests/integration/api/test_payroll_import_rows.py`: rewritten with
  `FakeTransactionalSessionScope` (records which `mode` `resolve()` was called with).
  Cases: commit by default and explicit, `validate` (full pipeline runs, but
  `resolve("validate")`), 422 on a null `concept_code`, 422 on an unknown `mode`, forced
  rollback when `from_rows()` fails (even though `commit` was requested), forced
  rollback when `ProcessImportedPayrollPeriods` fails due to a downstream dependency
  being down (502).
- 354 total tests, 100% coverage (`--cov-fail-under=100`), lint/typecheck/vulture clean.

### Pending / out of scope for this stage

- There is no end-to-end smoke test against pf-db's real schema (migrations + seeds for
  `PAY_CONCEPT`/plans) exercising the real HTTP route with no fakes. Confidence comes
  from three independent layers already covered: the SAVEPOINT mechanism itself (real
  Postgres), the route's orchestration (which `resolve()` gets called and when, fakes),
  and `import_rows()`/`ProcessImportedPayrollPeriods` being unchanged (already 100%
  covered before this feature). If that full smoke test is ever wanted, all the
  testcontainers infrastructure is already there to reuse.

## Closing gaps vs. the original design (2026-09-25)

After Stage 3 shipped, `pdf-import-design-recommendation.md` was reviewed end to end
against the real code (not just against what this document's "Overall status" table
said), and three real divergences from the approved design turned up. All three were
closed in the same session:

1. **`validate` no longer rejects an unresolved `concept_code` — only `commit` does.**
   Design section 3 is explicit: *"`commit` must fail; `validate` does not fail — it
   returns a warning with the detail"*. The original Stage 3 made `concept_code: str`
   mandatory at the schema level, rejecting with 422 in **both** modes alike — simpler,
   but not what the design asked for, and a real problem the day a new employer
   (without a complete template) needs to iterate with `validate` before everything is
   resolved.
   - `ImportPayrollRowRequest.concept_code` changed to `str | None` (still a required
     field in the JSON — it must be present — but it can now be `null`).
   - The route splits resolved from unresolved rows **before** calling any use case:
     `mode="commit"` with any unresolved row raises an explicit `PayrollValidationError`
     (400), naming the affected indexes, without touching `from_rows()`. `mode="validate"`
     runs the real pipeline only over the resolved rows (if any) and reports the rest in
     the new `ImportPayrollResponse.unresolved_rows` field (index + amount + period),
     without failing.
   - Explicit edge case covered: if `validate` receives rows but **none** has a resolved
     `concept_code`, the route never calls `from_rows()` (which would reject with "rows
     must not be empty", a confusing error for this case) — it returns
     `imported_periods=0` directly plus the warning. If instead the `rows` list is
     literally empty (`[]`), `from_rows([])` is still allowed to trigger its existing
     guard unchanged, to avoid altering that prior behavior.
   - New tests in `test_payroll_import_rows.py`: explicit rejection on `commit` (400,
     not 422), `validate` with a mix of resolved/unresolved (reports and continues),
     `validate` with everything unresolved (pipeline never gets called).
2. **New `payroll template-test <pdf>` CLI command.** Design section 5, point 4, asked
   for a command to iterate on a template without writing code
   (`payroll template test <pdf> --employer ...`). It didn't exist. `template-test` was
   added to `interfaces/cli/main.py` (without the `--employer` flag: template
   auto-detection already exists and is exactly what needs to be tested, forcing an
   employer up front would be testing something else) — it runs `PreviewPdfImport` with
   the real extractor (no DB, no persistence), prints a one-line summary
   (`template_id=... rows=N unresolved=M`) plus the detail of every unresolved row to
   `stderr`, and the full preview as JSON to `stdout`. Tested by hand against
   `secrets/Liquidación_202608.PDF`: `template_id=walmart-chile-v1 rows=13
   unresolved=0`, confirming the real template still resolves 100%.
3. **The pf-rates caveat now lives in the endpoint's public docstring.** The brief
   asked for it to be stated explicitly for whoever consumes the API (not only in the
   internal `.md` files). It was added to `ImportPayrollRowsRequest`'s docstring in
   `interfaces/api/routes/payroll.py` (visible in the service's OpenAPI/Swagger):
   neither `commit` nor `validate` undoes any market-data caching pf-rates may do in its
   own database while resolving a missing exchange rate/UTM.

**Verification:** 359 tests (+5 vs. Stage 3's close), 100% coverage,
lint/typecheck/vulture clean.

## Change log

- **2026-09-25 (cont. 7)** — Closed 3 gaps vs. the original design found during an
  end-to-end review (see "Closing gaps" above): `validate` no longer rejects an
  unresolved `concept_code` (only `commit` does, with an explicit 400 and
  `unresolved_rows` in the response), new `payroll template-test <pdf>` CLI command,
  and the pf-rates caveat now lives in the endpoint's public docstring. 359 tests,
  100% coverage, lint/typecheck/vulture clean.
- **2026-09-25 (cont. 6)** — Post-push fix: Stage 3's push broke CI (`gh run list`
  showed the run as `failure`). Cause: two tests in `test_payroll_import_rows.py`
  (the ones expecting 422 for an invalid body) had no dependency overrides, assuming
  FastAPI would short-circuit `Depends()` resolution before validating the body.
  **That's false** — FastAPI resolves the entire dependency tree (including
  `get_transactional_session()`, which opens a real connection via `engine.connect()`)
  before looking at the body's validation errors. Locally the tests still passed
  because an old SSH tunnel was listening on port 5432 pointing at a real database; on
  the GitHub Actions runner there's nothing there and it blows up with
  `OSError: Connect call failed`. Fix: both tests now also mock
  `get_transactional_session` (like the rest), and `scope.resolved_with == []` was
  added as an explicit assertion that the real transactional dependency is never
  touched in those cases. Verified by pointing `PF_DATABASE_URL` at a port that truly
  doesn't respond (simulating CI) before re-pushing — 354 tests, 100% coverage.
  **Lesson for future routes with eager-I/O dependencies:** never assume a "422 for
  invalid body" test can skip dependency overrides just because the handler will never
  use them — FastAPI instantiates them regardless.
- **2026-09-25 (cont. 5)** — Stage 3 complete: `TransactionalSessionScope` with
  SQLAlchemy's `join_transaction_mode="create_savepoint"`, the `POST
  /payroll/import/rows` route now truly supports `mode="validate"` (full pipeline, zero
  persisted writes). The first test against a real Postgres in all of pf-payroll
  (`testcontainers[postgres]`). 354 tests, 100% coverage, lint/typecheck/vulture clean.
  See the Stage 3 section above for detail.
- **2026-09-25 (cont. 4)** — Stage 2 complete: `ImportPayroll.from_rows()`, `POST
  /payroll/import/rows` route (`commit` mode only), reusing 100% of the existing
  pipeline. 345 tests, 100% coverage, lint/typecheck/vulture clean. See the Stage 2
  section above for detail.
- **2026-09-25 (cont. 3)** — Stage 1 complete: template-based extractor, a real
  WALMART-CHILE template, `POST /payroll/import/pdf-preview` endpoint with no
  persistence. 337 tests, 100% coverage, lint/typecheck/vulture clean. See the Stage 1
  section above for detail.
- **2026-09-25 (cont. 2)** — At the user's explicit request,
  `scripts/data-fixes/2026-09-fix-period-semantics-walmart-chile.sql` was removed from
  the repo (no longer needed: local, Neon, and the source CSV were already aligned).
  The SQL is documented above in case it ever needs to be rebuilt.
- **2026-09-25 (cont.)** — User confirmed the manual fix in Neon. Also fixed
  `secrets/payroll-input.csv` (the real source CSV for WALMART-CHILE's import,
  gitignored) with the same `period_year/period_month = payment_date`'s year/month
  recalculation. **Stage 0 fully closed** (code + all three data locations: local,
  Neon, source CSV).
- **2026-09-25** — Action plan kicked off. Stage 0 completed in code and in local data;
  Neon is pending explicit authorization. This tracking document was created (separate
  from the original proposal, which stays frozen).
