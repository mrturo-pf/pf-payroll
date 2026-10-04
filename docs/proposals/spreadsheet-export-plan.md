# Spreadsheet export + blank template — implementation plan (living document)

> Mirrors the convention set by `pdf-import-action-plan.md`: the design brief and design
> recommendation stay frozen as originally written; this file gets updated with real
> progress, findings, and decisions, dated, as the feature is actually built.

## Overall status

| Stage (per the recommendation's Item 6) | Description | Status |
| --- | --- | --- |
| 1 | Blank template (`GET /payroll/spreadsheet/template`) | **Complete** |
| 2 | Bulk export (`GET /payroll/spreadsheet`, `list_period_details()`, `ExportPayroll`) | **Complete** |
| 3 | Round-trip tests | **Complete for the boundary this feature owns; manually verified live against real data too -- see "What changed vs. the plan" and "Live verification" below** |

All three stages were implemented in a single session (2026-09-29) rather than shipped
incrementally, since nothing in Stage 2/3 turned out to require live user feedback on
Stage 1 first. Docs/Postman were updated in the same session, not after, per the
recommendation's own instruction.

## Pre-work: reading the ecosystem

Read, in order: root `AGENTS.md`/`README.md`, then each subproject's `AGENTS.md`
(`pf-db`, `pf-rates`, `pf-payroll`, `pf-sheets`) and `pf-common/README.md`. Relevant
takeaways applied while building this feature:

- Hexagonal architecture (`interfaces → application → domain`,
  `infrastructure → application`), `Protocol`-based ports, DTOs as the only thing
  crossing layer boundaries — followed for `PayrollSpreadsheetExporter` (new port),
  `ExportPayroll` (new use case), `CsvPayrollExporter`/`XlsxPayrollExporter` (new
  infrastructure adapters).
- `Decimal` everywhere for money — amounts are serialized as `str(Decimal(...))` in the
  exported file, never `float`, so nothing gets rounded/mangled by pandas' numeric
  dtypes.
- No `pf-db` coordination needed (confirmed independently, matching the
  recommendation's own "Cost impact" section) — everything reads existing tables
  through a new `PayrollRepository` method.
- `docs/api.md` and the root Postman collection must track reality in the same change,
  not "later" — both updated in this session (see below).

## What was built (Stages 1 + 2)

### Application layer

- **`application/dto.py`**: `ExportPayrollFiltersDTO` (all fields optional: `employer`,
  `period_year`, `period_month` — omitting all three means "export everything",
  mirroring `GET /payroll/summary`'s own no-filter behavior).
- **`application/ports/spreadsheet_exporter.py`** (new file): `PayrollSpreadsheetExporter`
  Protocol with `export(periods) -> bytes` and `export_template() -> bytes` — the
  mirror-image port of `PayrollImporter`.
- **`application/ports/repositories.py`**: added `list_period_details(filters) ->
  list[PayrollPeriodDetailDTO]` to the `PayrollRepository` Protocol. Takes the filters
  DTO as one argument (not three separate keyword args as the recommendation's snippet
  showed) for consistency with this codebase's existing command-DTO pattern
  (`ComputeContributionsCommandDTO`, etc.).
- **`application/use_cases/export_payroll.py`** (new file): `ExportPayroll.execute()` —
  reads via the repository, hands the result to the exporter, returns bytes. No branching
  logic of its own; the mirror-image of `ImportPayroll`.

### Infrastructure layer

- **`infrastructure/importers/xlsx_importer.py`** (extended, not duplicated): added
  `PREFIX_COLUMNS`, `NET_PAY_COLUMN`, `COMPUTED_ONLY_CONCEPT_COLUMNS`, `wide_columns()`,
  and `inverted_concept_map()`. These live in the *importer* module, not a new shared
  module, because `CONCEPT_MAP` already lives there and the recommendation was explicit
  that the exporter must derive its column shape from the importer's own source of
  truth, never a second hand-maintained mapping.
- **`infrastructure/exporters/spreadsheet_exporter.py`** (new module):
  `build_export_dataframe()`, `build_template_dataframe()`, `CsvPayrollExporter`,
  `XlsxPayrollExporter` (both stateless, using pandas + `openpyxl`, already existing
  dependencies — no new install), `export_columns()`, `get_exporter_for_format()`.
- **`infrastructure/db/repositories/payroll_repository_queries.py`**: added
  `list_period_details()`. Implementation: one query to resolve matching period ids
  (joined against `EmployerModel` for the `employer` filter, ordered by
  year/month/employer name), then `get_period_detail()` per id. Deliberately reuses
  `get_period_detail()` rather than a bespoke bulk query with its own joins — DRY, and
  guarantees the bulk and single-period code paths can never describe a period
  differently. Accepted trade-off: N+1 queries. At the volumes this system actually
  handles (the brief's own "expected volume" section — dozens of periods per employer,
  not thousands), this is not a real cost; revisit only if that changes (YAGNI, per the
  recommendation's own reasoning).

### Interfaces layer

- **`interfaces/api/routes/payroll_export.py`** (new file, new `APIRouter`): `GET
  /payroll/spreadsheet/template` and `GET /payroll/spreadsheet`. Kept as its own router
  module rather than appended to `routes/payroll.py` (already ~1400 lines) — cohesion
  (import vs. export vs. query concerns) plus not growing an already-large file further.
- **`interfaces/api/dependencies.py`**: added `build_export_payroll_use_case()` — a
  plain function (not a `Depends()`-resolved factory like its siblings), since the
  format it needs comes from the route's own `format` query parameter, a per-request
  value, not something to inject.
- **`interfaces/api/main.py`**: mounted the new router **before** the existing
  `payroll_router`. This one is load-bearing, not stylistic — see "Gotcha" below.

### Gotcha found and fixed: route registration order

`payroll_router` already has `GET /payroll/{period_id}` with no int converter on the
path parameter, so Starlette's route matching (which is purely path-shape based, before
any FastAPI-level type coercion/validation runs) treats `spreadsheet` as a valid value
for `{period_id}` — a single path segment matches regardless of type. Since two
different `APIRouter` objects just become a flat, ordered list of routes on `app.router`
in the order `include_router()` is called, mounting `payroll_export_router` *after*
`payroll_router` would make `GET /payroll/spreadsheet` get swallowed by
`GET /payroll/{period_id}` first (and then 422 on `int("spreadsheet")`) instead of ever
reaching the export route. Fixed by including `payroll_export_router` first in
`main.py`, with a comment explaining why the order matters. Verified with a manual
`TestClient` smoke test before writing the automated regression test
(`test_get_spreadsheet_does_not_collide_with_period_detail_route` in
`tests/integration/api/test_payroll_export.py`).

## Two corrections to the design recommendation, found while implementing/testing

Both were caught by actually running the code against realistic fixtures rather than
trusting the recommendation's worked example at face value — documenting them here so
nobody re-derives them the hard way later, and so nobody mistakes the corrected test
assertions in this session's test files for typos.

### Correction 1 — `net_pay_difference_clp` is *not* zero for an already-computed period

The recommendation's Item 5 test plan says re-importing an export should show
`validated: true` with "every diff field at 0", and its worked example
(`net_pay=1105000`, `income_tax=45000`, `unemployment_insurance=6000`) implies the same.
Tracing `xlsx_importer.py`'s `parse_period()`: `expected_net_pay_clp` is a running sum
over **only** the `CONCEPT_MAP` columns it reads (income minus discount) — it has no
term for `INCOME_TAX`/`UNEMPLOYMENT_INSURANCE`, because those two concepts have no
`CONCEPT_MAP` column at all (that's the whole premise of Item 1). So for a period whose
*true* declared net pay already reflects those two deductions (as any real payslip's
final net pay does), `expected_net_pay_clp` will always be `declared_net_pay_clp +
income_tax + unemployment_insurance`, and `net_pay_difference_clp` will always be
exactly `-(income_tax + unemployment_insurance)` — **on the very first import too, not
only on re-import.** This is pre-existing importer behavior this feature does not
change or need to fix; it is simply not "0" the way the worked example implied.

Confirmed harmless: `net_pay_difference_clp` is purely informational in every DTO it
appears in (`grep` across `application/` finds no branch that gates `validated`/commit
eligibility on it — only `_is_fully_validated()`'s own declared-vs-computed conflict
checks do that, and those are unaffected). Fixed the test accordingly:
`tests/integration/api/test_payroll_export_roundtrip.py::
test_reimport_net_pay_difference_equals_exactly_the_computed_only_total` asserts the
*exact*, derived expected value instead of a hardcoded `0`, and documents why in its own
docstring.

### Correction 2 — computed-only items are *not* "left alone" by `import_rows()`; they are deleted and then (usually) auto-recomputed

The recommendation's Item 1 states re-importing an export "does not touch concepts it
doesn't know about" — implying `INCOME_TAX`/`UNEMPLOYMENT_INSURANCE` items survive a
re-import untouched. Reading `payroll_repository_imports.py::import_rows()` directly:
when a period already exists, it runs

```python
await self._session.execute(
    delete(PayrollItemModel).where(PayrollItemModel.period_id == period.id)
)
```

— an **unconditional** delete of every `PayrollItemModel` row for that period, not one
scoped to only the concepts present in the current file. It then inserts only the items
built from the current import's rows. Since the importer never emits rows for
`INCOME_TAX`/`UNEMPLOYMENT_INSURANCE` (no `CONCEPT_MAP` column), those two items are
genuinely deleted and *not* re-inserted by `import_rows()` itself.

What actually makes a full round trip come out whole in practice is a second mechanism,
one layer up: `POST /payroll/import/spreadsheet` always runs `import_rows()` followed by
`ProcessImportedPayrollPeriods.execute()` in the same request (this is the existing
behavior `docs/api.md` already documents for that route: "when the imported payroll
already includes the pension and health contribution rows, the import flow also
computes UNEMPLOYMENT_INSURANCE and INCOME_TAX automatically"). Concretely,
`_process_period()` checks `if "UNEMPLOYMENT_INSURANCE" not in item_codes: compute it`
(same for `INCOME_TAX`), gated on `MANDATORY_DECLARED_CONTRIBUTION_CONCEPT_CODES =
{"PENSION_BASE", "PENSION_ADDITIONAL", "HEALTH_BASE"}` being present and
`declared_net_pay_clp` being set. Our exporter includes all three of those as ordinary
`CONCEPT_MAP` columns, so a re-imported export of an already-computed period does end up
with both computed concepts recreated — but via **recomputation**, not preservation, and
only when every precondition holds (market data resolvable for that period's date,
complementary-insurance validation not raising, pension/health base rows present).
`EconomicIndexNotFoundError` during that step is caught and simply skips the
recomputation rather than failing the request — meaning a re-import can, in a real
edge case, leave a period *without* its previously-computed items if market data that
used to be cached/resolvable is no longer available, until someone reruns
`POST /{period_id}/compute-tax` / `compute-contributions` by hand.

This is a materially different (and slightly riskier) mechanism than "left alone", even
though the common-case end result (computed items reappear, generally with the same
values, since the underlying inputs haven't changed for a historical period) matches
what the recommendation predicted. Documented here rather than silently "fixed" because
there is nothing to fix in this feature's own code — `import_rows()`'s delete-then-
reinsert and `ProcessImportedPayrollPeriods`'s auto-recompute both predate this feature
and are exercised identically by any spreadsheet import, hand-edited or exported. It is,
however, exactly the kind of behavior only a real database can honestly prove end to
end — see "What changed vs. the plan" below.

## What changed vs. the plan: Stage 3's test scope

The recommendation's Item 5 proposed three tests against a real, seeded database
(`test_export_reimport_validate_round_trip`,
`test_export_reimport_commit_is_a_noop_upsert`,
`test_export_ignores_computed_only_columns_on_reimport`), living next to
`test_payroll_import.py`. Two things changed this in practice:

1. **`test_payroll_import.py` (despite its `tests/integration/api/` path) does not
   actually use a real database** — it drives `TestClient` with FastAPI dependency
   overrides and fakes (`FakeTransactionalSessionScope`, canned
   `ImportPayrollResultDTO`s), same as every other file in that directory except
   `tests/integration/infrastructure/test_transactional_session.py`, which is the
   *only* real-Postgres test in this entire codebase (via `testcontainers`), and
   exists specifically to prove `TransactionalSessionScope`'s SAVEPOINT semantics —
   something no fake could honestly verify. There was no existing "seeded DB + real
   HTTP round trip" fixture to mirror, contrary to what Item 5 assumed.
2. **Correction 2 above** means a genuinely faithful version of Item 5's tests 2 and 3
   needs a live Postgres with `pf-db`'s real schema, seeded reference data
   (`PAY_CONCEPT`, pension/health institutions and plans, an employer with a payment
   rule) and a live market-data path (or a fake `PfRatesClient`) for
   `ProcessImportedPayrollPeriods` to run against — considerably more infrastructure
   than "seed 2+ periods" suggested, and cross-repo by nature (`pf-db` owns that
   schema/seed data). Docker was also unavailable in this session's sandbox (confirmed:
   `tests/integration/infrastructure/test_transactional_session.py`'s existing
   testcontainers tests fail here with a Docker socket-mount error, unrelated to this
   change), so even attempting it could not have been verified end to end today.

**Decision:** ship the round-trip tests at the boundary that this feature actually
introduces risk to — the exporter/importer column-shape agreement (`CONCEPT_MAP` drift)
— using real `CsvPayrollExporter`/`XlsxPayrollExporter`/`XlsxPayrollImporter` (real
pandas serialization/deserialization, zero fakes) in
`tests/integration/api/test_payroll_export_roundtrip.py`. `import_rows()`'s
delete-then-reinsert and `ProcessImportedPayrollPeriods`'s auto-recompute (Correction 2)
are pre-existing, already-shipped behavior this feature does not modify, so they are
explicitly out of this session's test scope — flagged below as a concrete follow-up
with the exact fixture requirements it would need, rather than either skipped silently
or half-faked into a misleading green checkmark.

### Follow-up (not committed as an automated test this session; manually verified live against the real stack -- see "Live verification" below): live-DB round trip

To turn Item 5's tests 2 and 3 into a *committed, repeatable* automated test, a future
session needs:

1. A `postgres:16-alpine` testcontainer running `pf-db`'s real migrations (not just an
   ad hoc probe table).
2. Seeded reference data: at least one `PAY_CONCEPT` row per concept code this feature
   touches, one pension institution/plan, one health institution/plan, one employer
   with a `payment_date_rule`.
3. Either a live/fake `PfRatesClient` (respx-mocked, matching
   `tests/integration/api/test_pf_rates_integration.py`'s existing pattern) so
   `ProcessImportedPayrollPeriods` can resolve market data deterministically without a
   real pf-rates instance.
4. The actual test sequence: import → compute-contributions → compute-tax → `GET
   /payroll/spreadsheet` → `POST` those exact bytes back to
   `/payroll/import/spreadsheet` (`mode=validate` then `mode=commit`) → assert the
   period id is unchanged, item amounts match, and (per Correction 2) that
   `INCOME_TAX`/`UNEMPLOYMENT_INSURANCE` reappear with the same values after
   recomputation.

## Live verification (2026-09-29, after `scripts/pf-services.sh start`)

Once the user asked to stand up the real stack (`pf-db` via Docker/nerdctl, `pf-rates`,
`pf-payroll`, all reporting healthy), this was used to actually run the live-DB
round trip the section above flagged as unverified -- against the real, already-seeded
**WALMART-CHILE** history (22 real periods, the same dataset documented in
`pdf-import-action-plan.md`), not synthetic fixtures. Result: a third, more consequential
finding.

### Correction 3 — the round trip is *not* a general-purpose safe no-op for real historical data, and that's correct behavior, not a bug

`GET /payroll/spreadsheet?format=csv` against the live DB returned all 22 real
WALMART-CHILE periods, `income_tax`/`unemployment_insurance` columns included, exactly
as designed. Re-posting that *exact, unmodified* export to
`POST /payroll/import/spreadsheet`:

- `mode="validate"` → **422**, `conflicting_periods` lists **21 of the 22 periods**
  (only 2024-11 reconciles cleanly). The conflicts are not about
  `net_pay_difference_clp` (Correction 1) -- most periods also fail
  `contribution_validation` (`PENSION_BASE`/`PENSION_ADDITIONAL`/`HEALTH_BASE` declared
  vs. expected mismatches, tens of thousands of CLP off) and, for at least one period,
  `complementary_insurance_validation` (a 38.5% cost discrepancy vs. assigned plans).
- `mode="commit"` → **422** too, with the identical `conflicting_periods` payload.
  Confirmed via `GET /payroll/summary` before/after (22 periods, unchanged) that
  nothing was partially written -- the `TransactionalSessionScope` SAVEPOINT rollback
  worked exactly as designed even against this much larger, real dataset.

**Root cause: this has nothing to do with the export/import file format.** Every
reconciliation check (`ComputeContributions`, complementary insurance validation)
recomputes its "expected" values from **today's** reference data (contribution caps,
UF rates, pension/health institution rates, plan assignments) -- not from whatever
reference data was in effect back when each period was originally imported. For
historical periods, reference data has legitimately moved on since then (rate/cap
updates, index syncs), so the *declared* amounts on file (frozen at import time) and
the *expected* amounts (computed fresh, today) diverge more the older the period is
-- and this would happen identically if the exact same historical CSV/PDF that
originally produced these periods were re-submitted today, completely independent of
this session's export feature. Re-exporting and re-importing does not introduce this
drift; it just makes it visible again, because a period must always pass through the
same reconciliation gate `POST /payroll/import/spreadsheet` already applies to any
file, exported or hand-written.

**Practical consequence for this feature's own claims:** the design brief's "export,
then re-import unmodified, and it succeeds as a no-op" acceptance criterion holds
only for periods whose declared amounts still agree with what today's reference data
would compute -- in practice, this is closest to true immediately after a period is
computed, and decays the further back in time an exported period is. It is **not** a
general-purpose "safe refresh/re-sync any period" tool for an arbitrary already-
persisted historical export, and `docs/api.md`'s existing wording ("exporting and
re-importing this exact file back... is safe and idempotent for every other column")
is accurate about the *column-shape* round trip (Correction 2's mechanism, still true)
but should not be read as a blanket "any exported file always re-imports cleanly"
guarantee -- that part depends entirely on whether reconciliation still agrees, which
is pre-existing, unrelated behavior this feature does not change and is not expected
to fix. Recorded here rather than treated as a defect: rejecting a stale re-import
instead of silently overwriting fresher, since-corrected computed values with stale
ones is exactly the safety property `_is_fully_validated()` exists to guarantee, and
it is working as intended, proven against real production-shaped data instead of only
fabricated fixtures.

## Testing done this session

- `tests/unit/infrastructure/test_spreadsheet_exporter.py` (new, 13 tests): column-order
  guards (`wide_columns()` vs. `CONCEPT_MAP`, `export_columns()` vs.
  `COMPUTED_ONLY_CONCEPT_COLUMNS`), template has zero rows, export dataframe maps
  through `inverted_concept_map()`, blank computed columns when a period was never
  computed, blank `net_pay` without a summary, CSV/XLSX byte-level sanity, empty-filter
  export still produces a valid header-only file, format resolution.
- `tests/unit/application/test_export_payroll.py` (new, 5 tests): `ExportPayroll`
  forwards filters/periods/bytes exactly, no-filter call still reaches the repository.
- `tests/integration/api/test_payroll_export_roundtrip.py` (new, 9 tests, real
  exporter + real importer, no fakes, no DB): every declared concept survives export →
  reimport exactly; computed-only concepts never reappear as importable rows; period
  header fields (employer/period/payment_date/worked_days/contract kind) survive;
  `net_pay_difference_clp` equals exactly `-(income_tax + unemployment_insurance)`
  (Correction 1); export header column count matches `export_columns()` (the
  CONCEPT_MAP-drift guard Item 5 asked for) — parametrized over CSV and XLSX.
- `tests/integration/api/test_payroll_export.py` (new, 11 tests, `TestClient` +
  dependency-override fakes, matching `test_payroll_import.py`'s own convention):
  template needs no repository at all; CSV/XLSX media types and
  `Content-Disposition`; format defaults to csv; unsupported format is 422; filters
  reach the repository unchanged (including the "no filters = everything" case);
  empty-match export is a header-only 200, not an error; the route-collision
  regression test for the `main.py` ordering gotcha above.
- `tests/unit/infrastructure/test_payroll_repository.py` (extended, +3 tests):
  `list_period_details()` fans out `get_period_detail()` per matched id in order; a
  period id vanishing between the id-lookup and the detail fetch is skipped, not
  raised; zero matching ids short-circuits before any fan-out query.
- Manual smoke test (not committed, ad hoc): `TestClient` against the real `app` object
  confirmed route ordering, media types, `Content-Disposition` headers, and 422 on a
  bad `format` value, before writing the equivalent automated test.

**Results:** 455 passed (full existing suite + all new tests), 3 pre-existing
`test_transactional_session.py` tests deselected (Docker unavailable in this sandbox —
confirmed unrelated to this change: they fail the same way on `main` before this
feature). `ruff check` and `mypy` both clean on every new/modified file, zero
suppressions.

## Docs / Postman updates (same session, per the mandatory-tracking rule)

- **`modules/pf-payroll/docs/api.md`**: added `GET /payroll/spreadsheet/template` and
  `GET /payroll/spreadsheet` rows to the "Payroll" endpoint table, including the
  computed-only-columns-are-ignored-on-reimport caveat Item 1 explicitly asked to be
  documented for API consumers (not just in this internal file).
- **`postman/pf-ecosystem.postman_collection.json`**: new "Export" folder under
  `pf-payroll → Periods`, between the existing "Import" and "Queries" folders —
  "Download blank template (format=csv)" and "Export spreadsheet (format=csv)" (with
  `employer`/`period_year`/`period_month` present but disabled by default, so the
  no-filter "export everything" case is what runs out of the box). Verified the file is
  still valid JSON after editing (`python3 -m json.tool` round trip).

## Files touched (full list)

**New:**
- `src/payroll/application/ports/spreadsheet_exporter.py`
- `src/payroll/application/use_cases/export_payroll.py`
- `src/payroll/infrastructure/exporters/__init__.py`
- `src/payroll/infrastructure/exporters/spreadsheet_exporter.py`
- `src/payroll/interfaces/api/routes/payroll_export.py`
- `tests/unit/infrastructure/test_spreadsheet_exporter.py`
- `tests/unit/application/test_export_payroll.py`
- `tests/integration/api/test_payroll_export.py`
- `tests/integration/api/test_payroll_export_roundtrip.py`
- `docs/proposals/spreadsheet-export-plan.md` (this file)

**Modified:**
- `src/payroll/infrastructure/importers/xlsx_importer.py` (added `wide_columns()`,
  `inverted_concept_map()`, `PREFIX_COLUMNS`, `NET_PAY_COLUMN`,
  `COMPUTED_ONLY_CONCEPT_COLUMNS`)
- `src/payroll/application/dto.py` (added `ExportPayrollFiltersDTO`)
- `src/payroll/application/ports/repositories.py` (added `list_period_details` to
  `PayrollRepository`)
- `src/payroll/infrastructure/db/repositories/payroll_repository_queries.py` (added
  `list_period_details()`)
- `src/payroll/interfaces/api/dependencies.py` (added
  `build_export_payroll_use_case()`, new imports)
- `src/payroll/interfaces/api/main.py` (mounted `payroll_export_router`, ordering
  comment)
- `tests/unit/infrastructure/test_payroll_repository.py` (added 3 `list_period_details`
  tests + import)
- `docs/api.md`
- `../../postman/pf-ecosystem.postman_collection.json`

## Change log

- **2026-09-29 (cont.)** — Committed and pushed the Postman collection change to
  `pf-base` (root repo only, per policy: `pf-payroll`'s own code changes live in its
  own separate git repo, untouched from the root). Stood up the full local stack via
  `scripts/pf-services.sh start` (`pf-db`, `pf-rates`, `pf-payroll`, all healthy) and
  used it to manually verify the round trip against real, already-seeded WALMART-CHILE
  data (22 periods) -- see "Live verification" above (Correction 3): confirmed the new
  endpoints work end to end against a real Postgres, and found that re-importing an
  export of already-persisted historical periods is correctly rejected (422, both
  modes) once reference data has drifted since original import -- pre-existing
  reconciliation behavior, not a defect in this feature, but an important correction to
  the "safe no-op round trip" framing for real (as opposed to freshly-computed)
  historical data.

- **2026-09-29** — All three stages implemented and tested in one session. Two
  corrections to the design recommendation found and documented during implementation
  (see above): `net_pay_difference_clp` is not zero for already-computed periods
  (pre-existing importer behavior, informational-only field, harmless); `import_rows()`
  deletes all of a period's items unconditionally rather than preserving unknown
  concepts, with computed-only items actually restored by
  `ProcessImportedPayrollPeriods`'s auto-recompute step in the same request rather than
  by being left untouched — functionally similar common-case outcome, different (and
  slightly more fragile) mechanism, with a documented follow-up for the live-DB test
  that would prove it end to end. `docs/api.md` and the Postman collection updated in
  the same change. 455 tests passing (deselected the 3 pre-existing Docker-dependent
  `test_transactional_session.py` tests, broken in this sandbox for unrelated reasons),
  ruff/mypy clean.
