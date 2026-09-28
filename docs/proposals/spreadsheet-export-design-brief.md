## Context

I want to build the mirror-image feature of what we just shipped: **pf-payroll** can
already *import* payroll periods from JSON, PDF, XLSX, and CSV. Now I want two related
backend-only capabilities:

1. **Export** persisted payroll periods back out as **XLSX and CSV**.
2. **Download a blank template** in the same two formats -- zero real data, just the
   correct column headers -- so I can fill it in by hand later and import it. This is a
   separate, much simpler capability from (1): it never reads a period from the
   database, it only needs to emit the exact same column shape (1) uses.

The scope of both is **backend-only**: the endpoint(s) and their contract. How the
response gets consumed (downloaded, opened in Excel, piped into another system) is not
part of this scope.

**This is NOT a from-scratch project.** Both halves of the machinery this needs already
exist in the code, in the opposite direction:

```python
# infrastructure/importers/xlsx_importer.py
CONCEPT_MAP = {
    "salary_base": ("SALARY_BASE", "income", True),
    "pension_base": ("PENSION_BASE", "discount", False),
    # ... one entry per wide-format column, mapping to (concept_code, kind, is_taxable)
}

def to_long_format(wide_df: pd.DataFrame) -> pd.DataFrame:
    """Pivots multi-column flat export into normalized application DTO formats."""
    # one row per period in wide_df -> N rows per period in long format,
    # one per concept_code, using CONCEPT_MAP to resolve column -> concept_code/kind
```

`XlsxPayrollImporter.read_rows()` reads a CSV/XLSX file (via `pandas.read_csv` /
`pandas.read_excel`), pivots it from **wide** (one row per period, one column per
concept) to **long** (one row per `concept_code`) using `CONCEPT_MAP`, and hands the
result to the same `POST /payroll/import/spreadsheet` pipeline used by
`POST /payroll/import/json`. **Export is exactly the inverse pivot**: read persisted
periods + items (long format, already in the database) and reassemble them into the
same wide-format shape `CONCEPT_MAP` already defines, so `pandas.DataFrame.to_csv()` /
`to_excel()` (via the already-installed `openpyxl>=3.1` engine — see `pyproject.toml`)
can write them out. No new parsing/serialization dependency is expected.

For serving the generated file over HTTP, I expect the same kind of hexagonal seam
already used elsewhere in this codebase for output-formatting concerns: a small
`PayrollSpreadsheetExporter` port (a `typing.Protocol`) with CSV and XLSX adapters, and
a use case that reads via `PayrollRepository`, builds the wide-format `DataFrame` using
the inverted `CONCEPT_MAP`, and hands the writer (filename + bytes) to a route that
wraps it in a `Response` with the right `media_type` and a `Content-Disposition:
attachment` header. The use case should not know or care which library the adapter
uses to actually write the CSV/XLSX bytes -- you choose the concrete module layout.

**Scope decision, already made: this is always a bulk export, never a single-period
one.** The goal is round-trip parity with `POST /payroll/import/spreadsheet`, which is
itself inherently bulk (one file, many periods) -- a single-period-only export would not
even let me test that round-trip against a realistic multi-period file. Don't propose
or compare a single-period variant; design directly for "however many periods match the
request goes into one file," including the trivial case of a filter that happens to
match exactly one period.

**Non-negotiable requirement: exporting a period and re-importing that exact file, with
zero manual edits, must succeed end to end.** This is not just "`mode="validate"`
returns `validated: true`" -- I checked, and `mode="commit"` must work too, because
re-importing an already-persisted period is not a duplicate-conflict in this codebase,
it's an **upsert**:

```python
# infrastructure/db/repositories/payroll_repository_imports.py, import_rows()
period_result = await self._session.execute(
    select(PayrollPeriodModel).where(
        PayrollPeriodModel.employer_id == employer.id,
        PayrollPeriodModel.period_year == year,
        PayrollPeriodModel.period_month == month,
    )
)
period = period_result.scalar_one_or_none()
if period is None:
    period = PayrollPeriodModel(...)          # first import: insert
    ...
else:
    period.payment_date = first_row.payment_date  # re-import: update in place
    ...
    await self._session.execute(
        delete(PayrollItemModel).where(PayrollItemModel.period_id == period.id)
    )                                          # + replace every item
```

Since an existing `(employer, period_year, period_month)` is updated in place rather
than rejected, re-committing an exported-then-unmodified file should be a true no-op in
practice (same data goes back in). If your design can't guarantee that for some column
or rounding reason, treat that as a real bug to fix before shipping, not a known
limitation to document around.

**On the route name — I want your recommendation, but I lean toward *not* including
"export" in the path.** We already have precedent for this in this exact codebase:
`GET /payroll/{period_id}` and `GET /payroll/summary` already return JSON — which is,
functionally, "exporting to JSON" — and neither says `export` anywhere ("export" is
implied by `GET`; the noun tells you what you get back). My starting proposal,
following that same idiom and mirroring the existing upload route's own name:

- `POST /payroll/import/spreadsheet` already exists (upload direction).
- `GET /payroll/spreadsheet` for the new download direction -- same noun, opposite verb
  and opposite direction, which is exactly the REST idiom for a resource representation
  (uploaded vs. downloaded), not a new "action" needing its own verb in the path.
- For the blank template, something nested under the same resource, e.g.
  `GET /payroll/spreadsheet/template` -- still no "export" anywhere, and it reads as
  "the spreadsheet resource, template variant" rather than a new, unrelated action.

Confirm or push back on this in your response -- if there's a reason `/payroll/export`
(or something else) is actually clearer, say so and justify it instead of taking my
framing as final.

## Important constraints

- **Round-trip parity is a real, testable acceptance criterion, not an aspiration --
  and it must hold for `mode="commit"`, not only `mode="validate"`** (see the upsert
  evidence in the Context section above: re-importing an already-persisted period
  updates it in place, so this is achievable, not aspirational). Today's session found
  *three separate* real bugs, all in the same family: a piece of derived/exported data
  silently drifting out of sync with the source of truth it was generated from (a stale
  `pf-db` seed script resurrecting an already-corrected row; a stale test fixture no
  longer matching the domain model it was built against; a `04_seed_real.sql`
  idempotency key broken by a bare `UPDATE`). An export feature is structurally exactly
  this risk, permanently: if `CONCEPT_MAP` or the wide-format column set ever changes on
  the import side without the export side changing in lockstep, exported files will
  either silently drop data or fail to re-import. The round-trip (export an existing
  period, re-import that exact file unmodified in `mode="validate"` *and* in
  `mode="commit"`, assert `validated: true` / a clean upsert with every diff at `0`) must
  be an automated test, not something eyeballed once and left to rot.
- **The blank template's column headers must come from the same single source as the
  real export's columns.** Hardcoding the template's header row independently (a second
  hand-written column list next to the real export's) is exactly the kind of "two things
  that should always agree but nothing enforces it" drift this session kept finding.
- **DRY the concept mapping, don't fork it.** The export writer must reuse
  `CONCEPT_MAP` (or a formal inverse built from it once, not hand-copied) instead of
  maintaining a second independent `concept_code -> column_name` mapping. Two mappings
  that *should* always agree but are free to drift apart is exactly the anti-pattern
  behind today's bugs.
- **Sensitive data.** Exported files carry real salary, contribution, and employer
  data. Any exported file used as a local test fixture (not a synthetic one) must live
  in the existing gitignored `secrets/`-style convention, same as
  `secrets/payroll-input.csv` today — never a git-tracked path, not even temporarily.
- **Existing hexagonal architecture applies unchanged:** `interfaces -> application ->
  domain`, `infrastructure -> application`. `domain/` has no I/O. Ports are
  `typing.Protocol`. `Decimal` for every amount, never `float`. No `assert` for
  production validation — use `application/errors.py`. No silent fallbacks: if a
  persisted item has no `CONCEPT_MAP` column to export into, that must be an explicit,
  visible decision (see item 1 below), never a silently dropped column.
- **Ecosystem documentation rule (non-negotiable, from the root `AGENTS.md`):** any new
  or changed endpoint requires `pf-payroll/docs/api.md` **and** the root
  `postman/pf-ecosystem.postman_collection.json` to be updated in the *same* change,
  not "later" — this exact rule already caught three undocumented endpoints in a past
  audit. Treat this as part of the deliverable, not a follow-up task.
- **Cost:** no new infrastructure is expected — `pandas` and `openpyxl` are already
  dependencies (used today by the import path). State explicitly in your response
  whether that holds, or what (if anything) needs adding.

## What I need

1. **Invert `CONCEPT_MAP` cleanly, and flag what doesn't survive the round trip.**
   Persisted periods carry more than what a caller ever declared: computed items like
   `INCOME_TAX`, `UNEMPLOYMENT_INSURANCE`, and contribution amounts the reconciliation
   pipeline fills in, plus period metadata (`status`, `reviewed`) that has no column in
   today's import-side `CONCEPT_MAP`. Decide and justify: does the export include
   computed-only concepts (as extra, clearly-labeled columns), and if a file with those
   columns is re-imported, should the importer ignore them, reject them, or something
   else? Answer this explicitly — don't let it be an implicit side effect of whatever
   columns happen to get written.
2. **Blank template endpoint.** A second, much simpler capability: download an empty
   CSV/XLSX with the exact right column headers (zero data rows, or exactly one
   illustrative example row -- your call, justify it), so I can fill it in by hand and
   import it later. Requirements: (a) it must not read any persisted period at all --
   it is a static shell, unlike the real export, which makes it independently
   shippable and trivially cheap; (b) its header row must be derived from the *same*
   inverted `CONCEPT_MAP` the real export uses -- one source of truth, not a second
   hand-written column list that can silently drift from the first (see "Important
   constraints" above); (c) same CSV/XLSX format-selection mechanism as the real export
   endpoint (item 3 below), for consistency; (d) propose the route -- its own path
   (e.g. `GET /payroll/spreadsheet/template`, per my starting proposal in the Context
   section) vs. a flag on the same export endpoint (e.g.
   `GET /payroll/spreadsheet?empty=true`) -- and justify the choice.
3. **Endpoint design.** Method, exact path, and how the output format (CSV vs.
   XLSX) is selected — evaluate at least: a `.csv`/`.xlsx` suffix per path, a
   `?format=csv|xlsx` query parameter, and `Accept` header content negotiation. Pick
   one and justify it, and give the final concrete path (building on or replacing my
   `/payroll/spreadsheet` starting proposal from the Context section above). Also
   specify any filters worth exposing (employer, date range, status) to select which
   periods go into the (always bulk, per the Context section) file — keep YAGNI in
   mind: only propose a filter that already has a concrete precedent elsewhere in this
   API (e.g. `GET /payroll/period-range`, `GET /payroll/summary`).
4. **Hexagonal placement.** Propose a `PayrollSpreadsheetExporter` port (a
   `typing.Protocol`) with CSV and XLSX adapters living next to `XlsxPayrollImporter`
   (`infrastructure/importers/xlsx_importer.py` or a sibling module), and a use case
   that reads via `PayrollRepository`, builds the wide-format `DataFrame` using the
   inverted `CONCEPT_MAP`, and delegates serialization to the port. State whether
   `PayrollRepository` already exposes what's needed to fetch multiple periods' full
   detail (items included) in one call, or whether it needs a new method. Note the
   blank template (item 2) does not need any of this -- flag it as a smaller, separate
   slice.
5. **Round-trip test plan.** The single most important acceptance criterion: export an
   existing multi-period test fixture, feed the exported bytes back into `POST
   /payroll/import/spreadsheet` completely unmodified, once in `mode="validate"`
   (assert `validated: true`, every diff at `0`) and once in `mode="commit"` (assert it
   persists cleanly as an upsert of the same data, per the Context section's evidence
   that re-importing an existing period updates it in place rather than conflicting).
   Describe how this test is structured and where it lives (unit vs. integration), and
   how it stays honest if `CONCEPT_MAP` changes later (i.e. it should fail loudly on
   drift, not silently pass with fewer columns).
6. **Action plan.** By stages — state whether the bulk export or the blank template is
   the smallest independently-shippable slice and do that first (my guess is the blank
   template, since it needs no `PayrollRepository` changes at all), and call out the
   `docs/api.md` + Postman update as part of Stage 1 deliverables, not an afterthought.
   Note any risk or cross-repo coordination (I don't expect any with `pf-db`, since this
   reads existing tables — confirm that assumption or correct it).

## Response format

- **Deliverable:** a new Markdown file (not an inline reply, not a code PR), placed in
  the **same folder as this document** (`docs/proposals/`).
- Start by confirming or revising the route-naming proposal from the Context section,
  with your reasoning.
- Include a worked example: take one real-shaped (but synthetic/anonymized) period and
  show both its exported CSV row and the columns your inverted `CONCEPT_MAP` produces
  for any computed-only concepts, so the round-trip question in item 1 is concrete, not
  abstract. Also show the blank template's exact header row (item 2) side by side with
  the real export's, to make the "same single source of columns" constraint concrete
  too.
- End with your recommendation (route shape) and why.

## Stack

- Language: Python (FastAPI, hexagonal, already established in pf-payroll).
- Storage: PostgreSQL via pf-db (same existing period/item schema, read-only for this
  feature — no new tables or migrations expected; confirm or flag if wrong).
- Serialization: `pandas` + `openpyxl` (already dependencies, used today by
  `XlsxPayrollImporter`) — no new dependency expected unless you find a concrete reason
  otherwise.
- Expected volume: same as the existing import flow (single-employer, dozens of periods
  total today, not a high-throughput bulk-export use case).
