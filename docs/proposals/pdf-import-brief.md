## Context

I want to build a feature so that **pf-payroll** can extract data from a payslip
(*liquidación de sueldo*) straight from its PDF, without typing the data by hand. The
scope of this work is **backend-only**: two endpoints and their contract. How that API
gets consumed is not part of this scope.

1. One endpoint receives a PDF and returns a **preview JSON** with the extracted data,
   per-field confidence, and concepts without a match (it persists nothing).
2. Another endpoint receives that JSON (possibly corrected by the caller before
   resending it) and, depending on the given mode, validates or validates-and-creates
   the payroll period.

**This is NOT a from-scratch project, and the step split already exists in the code.**
The current `POST /payroll/import` flow already chains two separate use cases:

```python
# 1) ImportPayroll.from_bytes() -- application/use_cases/import_payroll.py
async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
    rows = self._importer.read_rows(filename, content)   # parse -> rows
    return await self._repository.import_rows(rows)      # persist raw rows

# 2) ProcessImportedPayrollPeriods.execute() -- runs afterwards, in the same request
#    Computes AFP/health/unemployment/tax against pf-rates, compares declared vs.
#    computed, and builds expected_net_pay_clp / net_pay_difference_clp /
#    net_pay_warning / contribution_validation.
#    -> THIS is where the "reconciliation" already lives today. Not in import_rows().
```

The new PDF flow must fit into this same seam, not create a parallel pipeline:

- **Endpoint 1 (preview, no persistence):** a new extractor (infrastructure adapter)
  that reads the PDF and produces candidate rows — analogous to `read_rows()`, but
  enriched with per-field confidence and match status against the concept catalog (see
  section 3). **It must not call `repository.import_rows()` nor
  `ProcessImportedPayrollPeriods`.** There is no database write in this step, and the
  actual reconciliation (which depends on pf-rates) does not run here.
- **Endpoint 2 (confirm, with two modes):** reuses **the exact same sequence** already
  used by `/payroll/import` today (`from_rows()` + `ProcessImportedPayrollPeriods.execute()`),
  without reimplementing it. It receives `list[ImportPayrollRowDTO]` as JSON instead of
  a file. It supports two modes over that same sequence:
  - `validate`: runs both use cases inside a DB transaction and does a `ROLLBACK` at the
    end. Returns the same result (warnings, diffs, computed totals) but persists
    nothing.
  - `commit`: same, but does a `COMMIT`.

  Running the same code and deciding commit/rollback at the end (instead of maintaining
  two separate validation implementations) prevents "validate" and "persist" from
  drifting apart over time. See section 2 for the design detail and a real limitation
  to document: `ProcessImportedPayrollPeriods` calls pf-rates to resolve missing market
  data, and that call can cache data in **pf-rates'** own database (a different
  service, a different DB) — a rollback on the pf-payroll side does not undo that. It's
  harmless (it's public exchange-rate/UTM data), but `validate` mode is not 100%
  side-effect-free end to end, and that needs to be stated explicitly.

Concrete extension points to reuse (read before proposing anything):

- `payroll/application/ports/importers.py` → `PayrollImporter(Protocol)`, method
  `read_rows(filename, content) -> list[ImportPayrollRowDTO]`. Endpoint 1's PDF
  extractor is inspired by this port, but since the result includes confidence and
  unmatched concepts, it probably deserves its own richer DTO instead of forcing the
  flat `ImportPayrollRowDTO` — see section 3.
- `application/dto.py` → `ImportPayrollRowDTO` (one row per `concept_code` +
  `amount_clp` per period/employer). This is the input shape endpoint 2 expects.
- `GET /reference-data/payroll-concepts` → **closed, seeded** catalog of
  `concept_code`. Today there is no generic "other income/other discount" concept
  (see section 4).
- `application/use_cases/import_payroll.py` → `ImportPayroll.from_bytes()`. Endpoint 2
  needs a sibling entry point (e.g. `from_rows(rows: list[ImportPayrollRowDTO])`) that
  calls `self._repository.import_rows(rows)` directly, without going through any
  `PayrollImporter`.
- `application/use_cases/process_imported_payroll_periods.py` →
  `ProcessImportedPayrollPeriods.execute()`. This is the use case that does today's
  actual reconciliation. Endpoint 2 reuses it untouched, in both `validate` and
  `commit` mode.

Attached is my latest payslip as a real example, stored **outside the git repo** (see
the PII restriction below): `modules/pf-payroll/secrets/Liquidación_202608.PDF`

## Important constraints

- **PII — non-negotiable.** Payslips contain the national ID (RUT), salary, pension and
  health data. No real PDF, and no JSON/fixture derived from a real PDF, may live in a
  git-tracked folder, not even temporarily. Use a folder outside the repo (or an
  already-gitignored `secrets/`-style directory, like `pf-db/secrets/` elsewhere in this
  same ecosystem). Test fixtures that do get committed must use synthetic/anonymized
  data.
- A payslip's format can change from one month to the next without notice (new
  concepts, different order, redesign).
- The format can be completely different between employers.
- PDFs can be digital (with selectable text) or scanned (image).
- Endpoint 1 (preview) **must not persist anything**, nor have side effects on other
  existing payroll periods — it is idempotent and safe to call repeatedly with the same
  PDF.
- Endpoint 2 (confirm) in `validate` mode must leave the pf-payroll database untouched
  (a real rollback, not a separate check). The only accepted and documented side effect
  is market-data caching in pf-rates (see above).
- Any proposal for new infrastructure (OCR, LLM, extraction service) must state its
  **estimated cost** and cheaper alternatives evaluated (this ecosystem's rule: the
  cheapest option wins unless explicitly justified). If the solution uses an LLM, it
  must go through **AI Innovation Lab / AI Launchpad**, not a direct external provider.
- Existing hexagonal architecture: `interfaces → application → domain`,
  `infrastructure → application`. `domain/` has no I/O. Ports are `typing.Protocol`.
  `Decimal` for every amount, never `float`. No `assert` for production validation —
  use `application/errors.py`. No "silent fallbacks": a low-confidence field must be
  explicitly flagged in the response, never guessed.

## What I need

1. **Analysis of the attached PDF**
   - Identify every field present and group them: employer data, worker data, period,
     taxable income items, non-taxable income items, legal discounts (AFP, health,
     unemployment insurance, income tax), other discounts, and totals.
   - Map each identified field to an existing `concept_code` from
     `GET /reference-data/payroll-concepts`. Flag which ones have no match.
   - Flag which parts of the document look stable and which are likely to vary.

2. **Design of the two endpoints**
   - **Endpoint 1 (preview):** signature, request/response, and why it must not touch
     `PayrollRepository` nor `ProcessImportedPayrollPeriods`. The response must include,
     for each candidate row: extracted value, proposed `concept_code` (or `null` if no
     match), confidence score, and the original label text from the PDF.
   - **Endpoint 2 (confirm):** signature, request (the JSON, shaped as
     `list[ImportPayrollRowDTO]` or a direct wrapper, plus a `mode: "validate" |
     "commit"` field), and how it delegates 100% to `from_rows()` +
     `ProcessImportedPayrollPeriods` — without reimplementing validation or
     contribution computation. Detail the transaction mechanism (dry-run with
     `ROLLBACK` vs. `COMMIT`).
   - State whether it's better to reuse `POST /payroll/import` (also accepting JSON, not
     just multipart, plus the new `mode` parameter) or create new routes (e.g.
     `POST /payroll/import/pdf-preview` and `POST /payroll/import/rows`). Justify the
     choice.

3. **Handling unmatched concepts** *(a real gap, not an assumption)*
   - Today there is no catch-all concept for "other income/other discounts". Compare
     options: (a) add generic concepts to the seeded catalog via a pf-db migration,
     (b) mandatory manual mapping per employer template with a fallback that flags the
     row as unresolved in the response, (c) leave the row without a `concept_code` in
     the preview JSON and have endpoint 2 explicitly reject it if it arrives unresolved.
   - State the impact of each option on endpoint 2's `validate`/`commit` mode (e.g.:
     should `validate` fail if rows are left without a `concept_code`, or return a
     warning?).

4. **Extraction options (endpoint 1 only — preview)**
   Compare at least: template-based extraction (coordinates/regex/anchors), LLM
   extraction with a forced JSON output schema, OCR for scanned documents, and a hybrid
   approach. For each: expected accuracy, tolerance to format changes, **estimated
   monthly cost** at the volume stated in "Stack", implementation/maintenance
   complexity, and how to test it with `--cov-fail-under=100` without real data (golden
   fixtures + provider mocks). Keep in mind: extraction errors here are low-risk because
   endpoint 2 re-validates before persisting — 100% accuracy is not required in
   endpoint 1, but a good confidence signal is.

5. **Versionable template system**
   - Format and location: must live in `pf-payroll/infrastructure` (git-versioned
     config, JSON/YAML), **not in pf-db**.
   - How to automatically detect which template applies to a given PDF.
   - How to version templates when the format changes, and the fallback when none
     matches (in the worst case, the preview JSON comes back nearly empty — endpoint 1
     must never break because of this).
   - How to create/adjust a template without writing code.

6. **Testing the validate/commit mode**
   - How to test that `validate` mode truly leaves no residue in the database (e.g. a
     test that runs `validate`, counts rows before/after, runs `commit`, and only then
     sees the change).
   - How to mock the pf-rates call in these tests so they don't depend on the network.

7. **Action plan**
   By stages, with endpoint 1 (preview) as an independently deliverable MVP, followed
   by endpoint 2 in `commit` mode only, and then `validate` mode.
   State risks and what requires cross-repo coordination with pf-db (if option 3a is
   chosen).

## Response format

- **Deliverable:** this response must be a new Markdown file (not an inline reply nor a
  code PR), placed in the **same folder as this document** (`docs/proposals/`).
- Start with the analysis of the attached PDF, including the mapping to existing
  `concept_code`s and the list of unmatched fields.
- Use tables for comparing options (extraction and unmatched-concept handling),
  including estimated cost.
- Include an example of the preview JSON (endpoint 1, with confidence and unmatched
  fields) and an example of the confirmation JSON (endpoint 2, shaped as
  `list[ImportPayrollRowDTO]` + `mode`) — using generic field names, with no real
  amounts or national ID (RUT) from my payslip.
- End with your recommendation and why.

## Stack

- Language: Python (FastAPI, hexagonal, already established in pf-payroll).
- Storage: PostgreSQL via pf-db (same existing concept/period/item schema). Endpoint 1
  (preview) does not write to the database; endpoint 2 in `validate` mode writes and
  rolls back within a transaction.
- AI/OCR service: none contracted yet — evaluate via AI Innovation Lab if the LLM route
  is chosen.
- Expected volume: [fill in: payslips/month].
