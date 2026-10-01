# Net-pay prediction — extending to all 12 future months (implementation plan)

> Mirrors the convention set by `pdf-import-action-plan.md`,
> `spreadsheet-export-design-plan.md`, and `pdf-template-management-design-plan.md`:
> the brief/recommendation for a feature stay frozen as originally written; a
> separate `*-plan.md` gets updated with real progress, findings, and decisions,
> dated, as the feature is actually built.
>
> **Frozen input for this plan:**
> `net-pay-prediction-reimplementation-design-recommendation.md` — that document
> already carries its own `## 9. Implementation results` section covering **Stage
> 1** (restoring the first-future-period UF-based prediction after the `pf-rates`
> migration had silently stubbed it out). This plan covers **Stage 2**, a distinct,
> later user requirement: the other 11 future months, which Stage 1 (and the
> original pre-migration feature it restored) always left `null`.

## 1. The new requirement

Stage 1 restored `net_pay_clp` for exactly one future period (`month_offset == 1`)
in `GET /payroll/period-range`; the other 11 stayed `null` by design — there was no
UF-driven way to project further out, and projecting further was explicitly out of
scope for a bugfix restoring previously-shipped behavior (see Stage 1's Section 7).

The user then asked for those 11 months to carry real values too, under this rule:

1. Replicate the first future month's predicted amount across subsequent future
   months unchanged.
2. Except at a month flagged `increase: true` (the existing
   `is_increase_period()` logic, driven by the employer's `first_increase_period` /
   `increase_frequency` configuration) — there, step the amount up using IPC
   (`IPC_CL`) inflation between the last real increase month and the most recent
   published IPC value. The future increase month's own IPC is never known in
   advance, so only an IPC period `<=` the increase month may be used.
3. After stepping, replicate *that* new amount through subsequent months until the
   next increase month, repeating the same stepping rule there.

## 2. Design decisions

- **New pure function, not a method on the repository class.** `project_future_net_pay_series()`
  lives in `payroll_repository_shared.py` alongside `predict_next_period_net_pay()`,
  taking the already-computed `first_future_net_pay_clp`, the current period's
  year/month, the employer's increase configuration, and the `MarketDataRepository`
  — no `self`, no session. Same shape as its sibling function, same reason: it is
  pure sequencing/arithmetic over values already fetched elsewhere, not a query.
- **One extra `MarketDataRepository` method: `get_latest_economic_index(code)`.**
  `get_economic_index_value(code, year, month)` (already existed, used by
  `ComputeIncomeTax`/`DeflateAmounts`/etc.) answers "what was IPC in period X";
  this rule additionally needs "what is the *most recent* IPC we have at all",
  which is a materially different query (`ORDER BY period DESC LIMIT 1`, not a
  point lookup) — reusing the existing method would require the caller to already
  know which period is "latest", which is exactly the unknown. `PfRatesClient`
  implements it via the already-existing `GET /economic-indices?code=...` endpoint
  (confirmed via `pf-rates`' own `api.md` that this list endpoint already returns
  results ordered by period descending), taking the first element — no new
  `pf-rates` endpoint, no new `pf-rates` code at all.
- **HTTP client ordering trusted, not re-verified client-side.** `pf_rates_get_list()`
  (new, alongside the existing `pf_rates_get()`) returns the raw parsed list;
  `PfRatesClient.get_latest_economic_index()` takes `results[0]` directly rather
  than re-sorting. Re-sorting here would silently mask a real `pf-rates`-side
  ordering bug instead of surfacing it — if `pf-rates` ever changes that ordering,
  this should break loudly in integration, not degrade silently.
- **Anchor advancement semantics (the one genuinely subtle part):** after a
  successful step at increase month `M`, the "last known increase month" for all
  *subsequent* stepping decisions becomes `M` itself, not the employer's original
  `first_increase_period`. This matters for configurations with
  `increase_frequency < 12` (verified by
  `test_project_future_net_pay_series_handles_consecutive_increase_months`, a
  1-month-frequency fixture): the second step's IPC baseline is the first step's
  own increase month, not the employer's originally configured anchor, otherwise
  consecutive increases would double-count the same inflation window.
- **Graceful degradation, consistent with Stage 1's philosophy:** if
  `market_data_repository` is `None`, or the first-future prediction itself is
  `None` (Stage 1 already degraded, e.g. a `pf-rates` outage), or an increase
  month's IPC data (either the baseline or the latest) is missing, or the only
  "latest" IPC on record is *after* the increase month being evaluated (should not
  happen in practice — IPC is only ever published for the past — but checked
  explicitly per an explicit user requirement, not assumed) — the series simply
  replicates the last known value unchanged rather than raising or returning
  `null`. Nothing in this rule involves a hard failure mode the way a `pf-rates`
  outage does in Stage 1 (which propagates `PayrollDependencyError`); IPC being
  one month behind or absent for a brand-new index code is a normal, expected
  steady state, not an error.
- **Fetched once per call, not once per candidate increase month.** The "latest
  published IPC" is the same real-world fact regardless of which future month is
  being evaluated in a given `list_period_ranges()` call, so
  `get_latest_economic_index()` is called exactly once at the top of
  `project_future_net_pay_series()` and reused across every month in the 2–12
  loop — both for correctness (a single `list_period_ranges()` response should not
  mix two different "as of" snapshots of the same index) and for the same
  `MarketDataRepository`-level TTL-caching reasons Stage 1 already relies on.

## 3. What was built

- **`payroll/shared/dates.py`**: `is_increase_period()` (already existed from Stage
  1's prerequisite work) and the new `resolve_last_increase_period()` — given
  `first_increase_period`, `increase_frequency`, and an `as_of` date, returns the
  most recent increase month at or before `as_of`, or `None` if `as_of` predates
  `first_increase_period` entirely (no real increase has happened yet).
- **`application/ports/repositories.py`**: `MarketDataRepository` gained
  `get_latest_economic_index(code) -> tuple[date, Decimal] | None`.
- **`infrastructure/http/_http_client.py`**: added `pf_rates_get_list()` alongside
  the existing `pf_rates_get()`, sharing the same `_pf_rates_request()` core (error
  handling, status-code branching) — the only difference is `.json()`'s static
  shape (`list[dict]` vs `dict`), so the branching logic itself was never
  duplicated, only the thin return-type wrapper.
- **`infrastructure/http/pf_rates_client.py`**: `PfRatesClient.get_latest_economic_index()`,
  TTL-cached exactly like its siblings, parsing the first element of
  `GET /economic-indices?code=...`.
- **`infrastructure/db/repositories/payroll_repository_shared.py`**:
  `project_future_net_pay_series(first_future_net_pay_clp, *, current_year,
  current_month, first_increase_period, increase_frequency,
  market_data_repository) -> dict[int, Decimal]`, keyed by `month_offset` (2..12).
  Internally: fetch latest IPC once; walk `month_offset` 2..12; on an increase
  month with usable IPC data, step and advance the anchor; otherwise carry the
  current value forward unchanged.
- **`infrastructure/db/repositories/payroll_repository_queries.py`**: `list_period_ranges()`
  now calls `project_future_net_pay_series()` once (right after computing
  `first_future_net_pay_clp`) and the `future_ranges` comprehension's
  `net_pay_clp` for `month_offset != 1` reads from that dict (`.get(month_offset)`,
  `None` only if the dict legitimately has nothing for that offset — it never does
  today, since the function always returns all of 2..12, but `.get()` keeps the
  call site correct even if that ever changes). The comprehension's `increase=`
  field now also calls the shared `is_increase_period()` directly instead of a
  (now-removed) private `self._is_increase_period()` method that duplicated it.

## 4. Findings from live verification against real Neon data

After the user's earlier local-DB restore (documented in the frozen
recommendation doc's own history), the restored database happened to contain a
*perfect* real-world exercise of this exact rule end-to-end, with no synthetic
fixture needed to sanity-check the math by hand:

- Employer 9 (`WALMART-CHILE`): `first_increase_period = 2026-04`,
  `increase_frequency = NULL` → defaults to 12 months.
- Current period: 2026-09, declared net pay `3,001,910.00`.
- `resolve_last_increase_period(first=2026-04, freq=12, as_of=2026-09)` → `2026-04`
  (0 full 12-month cycles have elapsed since).
- Next increase month within the 12-month future window: `2027-04`
  (`month_offset = 7`).
- `RAT_ECON_INDEX` (`pf-rates`' table) had real `IPC_CL` data through `2026-08`:
  `IPC_CL(2026-04) = 112.18`, latest published `IPC_CL(2026-08) = 113.15`.

Hitting `GET /payroll/period-range` against the locally running stack (`pf-rates`
on `:8001`, `pf-payroll` on `:8000`, both against the restored Neon-dump DB)
returned exactly:

| Period | `net_pay_clp` | `increase` |
| --- | --- | --- |
| 2026-09 (current) | `3,001,910.00` (declared) | `false` |
| 2026-10 (`month_offset=1`) | `3,118,248.98` (Stage 1 prediction) | `false` |
| 2026-11 .. 2027-03 (`month_offset=2..6`) | `3,118,248.98` (replicated) | `false` |
| 2027-04 (`month_offset=7`) | `3,145,211.91` (**stepped**) | `true` |
| 2027-05 .. 2027-09 (`month_offset=8..12`) | `3,145,211.91` (replicated) | `false` |

Hand-verified: `3,118,248.98 × (113.15 / 112.18) = 3,145,211.9102...`, which
quantizes to `3,145,211.91` — exactly the value the running service returned.
This confirms the whole chain (increase-month detection → IPC baseline lookup →
latest-IPC lookup → ratio application → quantization → replication) end-to-end
against production-shaped data, not just unit fixtures.

## 5. Tests added

- `tests/unit/infrastructure/test_payroll_repository.py`:
  - `FakeMarketDataRepository` extended with `economic_index_by_period` and
    `latest_economic_index` constructor args plus a real
    `get_latest_economic_index()` implementation (previously only
    `get_exchange_rate_value`/`get_economic_index_value` existed on the fake).
  - Eight focused unit tests directly against `project_future_net_pay_series()`:
    no-first-prediction, no-repository, the happy-path replicate-then-step case
    (mirroring the real Employer 9 numbers above, including the exact ratio),
    no-prior-increase (current period predates `first_increase_period`),
    no-latest-IPC, no-baseline-IPC, latest-IPC-later-than-the-increase-month (the
    explicit never-should-happen guard), and consecutive increase months
    (`increase_frequency=1`) proving the anchor correctly advances to the
    just-stepped month rather than staying pinned to the employer's original
    `first_increase_period`.
  - One `list_period_ranges()`-level integration-style test
    (`..._projects_all_future_months`) reusing the existing current/previous
    period fixture, wiring a full `FakeMarketDataRepository`, and asserting the
    real response rows (`result[13]` through `result[24]`) replicate and step
    exactly as expected — catching any future wiring regression between the
    standalone function and its call site, not just the function in isolation.
- `tests/unit/infrastructure/http_clients/test_pf_rates_client.py`: five new
  tests for `get_latest_economic_index()` — 200-with-data (parses the first
  element), 200-with-empty-list (a real, valid "no data yet" response, distinct
  from 404), 404, TTL caching, and 5xx propagating `PayrollDependencyError`.

## 6. One mypy fix along the way

`pf_rates_client.py`'s original 404/empty-list handling path called
`int(latest["period_year"])` where `latest: dict[str, object]` — `int()` has no
overload accepting plain `object`, and the pre-existing `# type: ignore[arg-type]`
silenced the *wrong* error code (mypy actually raised `call-overload`, not
`arg-type`, so the ignore comment was doing nothing). Fixed by routing through
`int(str(latest["period_year"]))` instead — `str(x)` accepts any `object` and
returns `str`, which *is* a valid `int()` overload input, so no suppression
comment is needed at all. (This repo's `AGENTS.md` forbids `assert` for
production validation, which was the other option considered and rejected here.)

## 7. Validation

- `pytest`: **518 passed** (510 before this stage), 100% coverage
  (`--cov-fail-under=100` green).
- `ruff check` / `ruff format --check`: clean.
- `mypy src`: clean, 97 source files, 0 errors.
- `vulture src --min-confidence 80`: clean.
- `jscpd`: **not run** — `npx jscpd` failed with `407 Proxy Authentication
  Required` fetching the package from `registry.npmjs.org`, and no local/global
  install of `jscpd` was available in this environment to fall back to. This is
  the same class of corporate-proxy friction already documented in the root
  `AGENTS.md` for `gh`; unlike that case there is no known env-var workaround for
  `npx` itself. Flagging this as something CI will catch (the pipeline's own
  `jscpd` step runs in an environment without this restriction) rather than
  silently skipping the concern — the new code was deliberately kept small and
  factored to avoid duplication (shared `_pf_rates_request()` core, shared
  `is_increase_period()` helper, no copy-pasted branching between
  `pf_rates_get()`/`pf_rates_get_list()`), so risk of a real duplication flag is
  low, but this should still be confirmed once the branch reaches CI.

## 8. Docs updated

- `docs/api.md`: `GET /payroll/period-range`'s row rewritten to describe the full
  prediction/replication/stepping behavior for `future` entries (previously only
  described the endpoint's shape, not Stage 1's or Stage 2's prediction
  semantics at all).
- Postman collection: no change needed — the "Period range (current + 12
  prev/next)" request already carries an empty `description` field (consistent
  with most other `Queries` folder entries), and neither the request shape nor
  any parameter changed, only server-side response semantics.

## 9. Not yet done

Per this repo's `AGENTS.md`: no commit, no push, in either `pf-payroll` or the
root `pf-base` repo. Awaiting explicit user authorization. Local working tree
changes are confined to `pf-payroll` only (`docs/api.md`,
`application/ports/repositories.py`, `infrastructure/db/repositories/
payroll_repository_{queries,shared}.py`, `infrastructure/http/{_http_client,
pf_rates_client}.py`, `shared/dates.py`, and the two test files listed in
Section 5) — nothing in `pf-db`, `pf-rates`, or the root repo was touched for
this stage.

## 10. Follow-up: deflation floor (2026-10-01, same session)

After reviewing the Stage 2 behavior live (the Employer 9 worked example in Section
4), the user asked for one more rule: **if the IPC step would be a decrease, hold
the previous net pay flat instead of reducing it.** Rationale given: Chilean
salaries are sticky downward in practice — employers do not cut pay just because
measured inflation between two dates came in negative (or the latest published
figure dipped below an earlier one due to revision/seasonality).

### Design

Treated as one more entry in `_apply_ipc_step()`'s existing degrade-gracefully
chain (no prior increase to anchor on → no IPC data at all → zero/negative
baseline → **now also: latest IPC below the baseline**) — all four conditions
share the exact same response shape: return `(current_value, last_increase_period)`
unchanged, i.e. hold flat *and* do not advance the anchor. Not advancing the
anchor on a skipped deflationary step is deliberate and mirrors the existing
missing-data paths: if prices later rebound above the *original* last-real-increase
baseline, the next increase month still compares against that same original
baseline rather than the skipped one, so no inflation "owed" to the employee is
silently lost to a temporary dip.

Guard condition: `latest_value < last_increase_index` (strict `<`). A **tie**
(`latest_value == last_increase_index`) is explicitly *not* treated as deflation —
it still runs the normal step path, which happens to be a numerical no-op (ratio of
exactly 1) but *does* advance the anchor, same as any other successful step. This
distinction only matters for anchor bookkeeping across multiple increase cycles;
covered by its own test (`..._steps_on_flat_ipc`) precisely so the `<` vs `<=`
choice is pinned down and cannot silently regress to the wrong comparison later.

### What changed

- `payroll_repository_shared.py::_apply_ipc_step()`: one new early-return guard,
  plus docstring updates explaining the floor and its anchor-preservation
  rationale.
- Two new tests in `test_payroll_repository.py`:
  `test_project_future_net_pay_series_holds_flat_on_deflation` (IPC 110.00 vs
  baseline 112.18 — holds flat) and
  `test_project_future_net_pay_series_steps_on_flat_ipc` (exact tie — steps
  normally, confirming the guard is `<` not `<=`).
- `docs/api.md`: `GET /payroll/period-range`'s row extended with one clause
  describing the floor.
- No change to `_apply_ipc_step()`'s signature, `project_future_net_pay_series()`,
  or the `list_period_ranges()` call site — this is a pure refinement of one
  existing internal decision point.

### Validation

- `pytest`: **520 passed** (518 before this follow-up), 100% coverage maintained.
- `ruff check` / `ruff format --check` / `mypy src` / `vulture src`: all clean.
- Live-verified against the restored Neon data once more after restarting the
  local `pf-payroll` service: Employer 9's real numbers are unaffected (real
  `IPC_CL` went **up** from 112.18 to 113.15, so the floor correctly does not
  trigger) — `2027-04` still shows `3,145,211.91`, confirming this change is a
  pure no-op for the already-verified real-world case and only activates on
  genuine deflation.
- `jscpd`: still not run locally (same `407` proxy block on `npx` documented in
  Section 7 above) — single-guard-clause change, negligible duplication risk,
  to be confirmed in CI same as the rest of Stage 2.

### Not yet done

Same as Section 9: no commit, no push, awaiting explicit user authorization.

## 11. Follow-up: `net_pay_clp` as a rounded JSON number, not a string (2026-10-01)

The user noticed `GET /payroll/period-range`'s `net_pay_clp` was a JSON string
(e.g. `"3145211.91"`, matching every other money field in this API) and asked for
a plain, rounded (no-decimals) JSON number instead for this specific field.

### Design

- Scoped to exactly one response field: `PayrollPeriodRangeRead.net_pay_clp`
  (`interfaces/api/routes/payroll.py`). Every other money field in the API
  (`PayrollSummaryRead.net_pay_clp`, `amount_clp`, `declared_net_pay_clp`, etc.)
  keeps its existing `str`-serialized `Decimal` convention -- that convention
  exists to round-trip exact precision over JSON (which has no native arbitrary-
  precision decimal type), and nothing about those other fields changed. This
  field is different in kind: it is a *projection/approximation* for `future`
  entries, already explicitly documented as such, never persisted or reconciled
  against -- exact-precision round-tripping was never a real requirement for it,
  unlike a real ledger amount.
- Rounding reuses the existing `domain/quantizers.py::quantize_clp()` helper
  (`value.quantize(Decimal("1"))`, i.e. default-context rounding to the nearest
  whole peso) rather than inventing a new rounding scheme -- this is the exact
  same helper `tax_calculator.py`/`contribution_calculator.py`/`deflation.py`
  already use for "round a CLP `Decimal` to zero decimal places" everywhere else
  in this codebase (DRY: one rounding convention for CLP, not two). The route
  layer already imports from `domain/` directly elsewhere in this same file
  (`EmploymentContractKind`), so this does not introduce a new architectural
  dependency direction.
- `int(quantize_clp(...))` converts the quantized `Decimal` (now an integral
  value) to a plain Python `int`, which FastAPI/Pydantic serializes as a bare
  JSON number.

### What changed

- `interfaces/api/routes/payroll.py`: `PayrollPeriodRangeRead.net_pay_clp` type
  `str | None` -> `int | None`; its construction in
  `to_payroll_period_range_reads()` now does
  `int(quantize_clp(item.net_pay_clp)) if item.net_pay_clp is not None else None`
  instead of `str(item.net_pay_clp)`.
- `tests/integration/api/test_payroll_queries.py`: the one JSON-literal assertion
  covering this field in the `/payroll/period-range` array updated from
  `"net_pay_clp": "830000"` to `"net_pay_clp": 830000` (unquoted). The other two
  `"net_pay_clp": "830000"` occurrences in the same file (`/payroll/summary` and
  `/payroll/{period_id}`'s embedded `summary`) are `PayrollSummaryRead`, a
  different schema, deliberately left untouched.
- `docs/api.md`: `GET /payroll/period-range`'s row gained a clause flagging that
  `net_pay_clp` is, uniquely in this API, a plain rounded JSON number rather than
  a precision-preserving string.
- Postman collection: no change -- the "Period range" request already carries an
  empty description, same as the Stage 2 update.

### Validation

- `pytest`: **520 passed**, 100% coverage maintained.
- `ruff check` / `ruff format --check` / `mypy src` / `vulture src`: all clean.
- Live-verified against the restored Neon data once more: `net_pay_clp` values
  now come back as bare numbers (`3118249`, `3145212`, etc. -- correctly rounded
  from `3118248.98`/`3145211.91`), confirmed via raw JSON inspection (no
  surrounding quotes), not just a Python-side `repr()` check that could mask a
  str-that-looks-like-a-number.

### Not yet done

Same as Sections 9/10: no commit, no push, awaiting explicit user authorization.
