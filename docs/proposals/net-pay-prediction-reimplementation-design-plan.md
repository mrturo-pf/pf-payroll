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

## 12. `increase` becomes a percentage, not a boolean (2026-10-01)

User request: `increase` on `GET /payroll/period-range` should stop being a
boolean and instead be a number (2 decimals) representing the percentage
variation of `salary_base` vs. the immediately preceding period in the
window.

### Design

Two genuinely different computations feed this one field, depending on
position -- this duality already existed before today (as a boolean), it is
not new:

- **`previous`/`current`**: `_compute_increase()` (routes/payroll.py) already
  compared `(salary_base / worked_days) * 30` between a period and its
  predecessor to decide True/False. Converting to a percentage was a small,
  low-risk change: same normalization, same insufficient-data guards (no
  predecessor in window / missing `salary_base`/`worked_days` on either
  side), new guard added for a zero-salary predecessor baseline (percent
  change from zero is undefined -- avoids a `ZeroDivisionError`). Returns
  `Decimal | None`, quantized via the new `quantize_percent()` helper.
- **`future`**: previously `is_increase_period()`'s raw calendar boolean
  (today's cadence check), completely decoupled from any real number.
  Reusing the *same* IPC ratio already computed to step `net_pay_clp`
  (`_apply_ipc_step()`) was the natural choice -- it means a stepped future
  month and its displayed `increase` percentage always agree numerically,
  instead of exposing two unrelated signals that happened to both be
  truthy at the same month.

### What changed structurally

- **New**: `domain/quantizers.py::quantize_percent()` -- same pattern as
  `quantize_clp()`/`quantize_utm()`, 2 decimal places.
- **`_apply_ipc_step()`** (payroll_repository_shared.py) now returns a
  3-tuple `(value, anchor, increase_pct)` instead of 2. `increase_pct` is
  computed from the *raw* ratio independently of whatever rounding happens
  to `value` -- deliberately NOT derived by re-applying the already-
  quantized percentage to `current_value`, which would have silently
  introduced a second, compounding rounding error (verified by hand: at
  112.18→113.15 the raw-ratio net_pay is 3,145,211.91, but reapplying the
  *quantized* 0.86% would have produced ≈3,145,065.92 instead -- a ~146
  peso discrepancy for no reason). `current_value` is now `Decimal | None`
  (see below) -- a `None` running value still gets a real `increase_pct`
  computed from IPC alone, it just can't be stepped into an actual amount.
- **Renamed**: `project_future_net_pay_series()` → `project_future_months()`.
  Now returns `dict[int, ProjectedFutureMonth]` (new frozen dataclass:
  `net_pay_clp: Decimal | None`, `increase_pct: Decimal`) covering
  month_offset **1 through 12** (previously 2-12 only -- month_offset 1's
  `net_pay_clp` was always special-cased to the UF prediction anyway, but
  its `increase` boolean used to be computed completely separately, via a
  second `is_increase_period()` call directly in
  `list_period_ranges()`'s comprehension). Folding offset 1 into the same
  walk removes that duplication and -- as a side benefit -- correctly
  carries forward an anchor-advance even in the near-impossible edge case
  where offset 1 itself lands on a scheduled increase month (see the
  function's own docstring for the full reasoning); `net_pay_clp` displayed
  for offset 1 is *still* always pinned to the raw UF prediction either
  way, so this is not a behavior change for any realistic configuration
  (`increase_frequency` would have to be 1 or very unusual for offset 1 to
  ever coincide with an increase month at all).
- **New**: `degraded_future_months()` -- extracted because the "no
  quantifiable data" fallback dict (month_offset 1 keeps its net_pay,
  2-12 go `None`, `increase_pct` is `0.00` everywhere) is needed in *two*
  places that must stay in sync: `project_future_months()` itself (no
  `market_data_repository` wired in at all) and the new
  `SqlAlchemyPayrollQueryRepository._project_future_months_or_degrade()`
  (a `market_data_repository` *is* wired in, but it raised
  `PayrollDependencyError` -- pf-rates is unreachable *right now*). Both
  failure modes look identical to the caller, so they degrade identically.
- **Bug caught by the test suite, fixed before merge**: the very first
  version of `project_future_months()` dropped the old function's
  `first_future_net_pay_clp is None -> return {}` short-circuit (replaced
  by decoupling `net_pay_clp` and `increase_pct`'s failure modes, see
  above), which meant it now unconditionally called
  `market_data_repository.get_latest_economic_index()` whenever a
  repository was wired in -- even when `predict_next_period_net_pay()` had
  *already* caught a `PayrollDependencyError` for that exact repository
  moments earlier in `list_period_ranges()`. A `FakeMarketDataRepository`
  configured to raise on every single call (simulating a full pf-rates
  outage, not just the UF endpoint) made this crash
  `test_list_period_ranges_degrades_to_none_on_market_data_outage`
  immediately. Fixed by adding
  `_project_future_months_or_degrade()` as `list_period_ranges()`'s *own*
  try/except boundary around this call -- same established convention as
  `predict_next_period_net_pay()`'s own try/except just above it in the
  same method (pure port-calling functions propagate
  `PayrollDependencyError`; `list_period_ranges()` alone is responsible for
  catching it and degrading).
- **`PayrollPeriodRangeDTO.increase`**: `bool | None` -> `Decimal | None`.
- **`PayrollPeriodRangeRead.increase`**: `bool | None` -> `float | None` (a
  plain JSON number, same `float(...)`-conversion pattern already used for
  `net_pay_clp` -- not a string, per the user's explicit ask).
- **`routes/payroll.py`**: `to_payroll_period_range_reads()` no longer
  wraps the future branch in `bool(...)`; it passes `item.increase` through
  (already the right type from the DTO).

### Tests

- 10 unit tests directly against the renamed `project_future_months()`
  rewritten/renamed to assert `.net_pay_clp`/`.increase_pct` on
  `ProjectedFutureMonth` objects instead of raw `Decimal` dict values, and
  widened from `len(result) == 11` (month_offset 2..12) to
  `len(result) == 12` (1..12) where applicable.
- 2 new/renamed tests specifically for the two "no quantifiable data"
  degradation paths (`test_project_future_months_nulls_net_pay_without_
  first_prediction`, `test_project_future_months_degrades_without_
  repository`) replacing the old "`returns {}`" assertions, since neither
  path returns an empty dict anymore.
- `test_sqlalchemy_payroll_repository_marks_scheduled_future_increases`:
  its assertions now expect `Decimal("0.00")` uniformly (this test's
  repository has no `market_data_repository` wired in, same as most other
  `list_period_ranges()` tests in this file) -- its docstring was rewritten
  to explain this honestly rather than pretend it still exercises the
  schedule-to-percentage path (that path is covered with real market data
  by `test_sqlalchemy_payroll_repository_lists_period_ranges_projects_all_
  future_months` instead, whose own `result[20].increase` assertion is now
  `Decimal("4.76")`, i.e. `(110.00/105.00 - 1) * 100`).
- `_compute_increase()`: renamed/rewritten True/False tests to assert
  `Decimal("20.00")` / `Decimal("-16.67")`; added a new
  `test_compute_increase_returns_none_for_zero_salary_predecessor` for the
  new zero-baseline guard (division-by-zero coverage gap caught by the
  100%-coverage gate).
- Full API-level JSON-literal and `SalaryFakeQueries`/`LookbackFakeQueries`
  integration tests in `test_payroll_queries.py` updated to the real
  computed percentages for their fixture data (20.0, 25.0, 0.0, etc.).

### Validation

- `pytest`: 518 tests passing (520 total minus 3 pre-existing
  Docker/testcontainers-dependent tests unrelated to this change --
  confirmed via `git stash` that they fail identically on `main` before
  this session's commits, root cause a local Docker Desktop mount-path
  error, not code), 100% coverage on every file this change touched
  (`interfaces/session.py`'s 54% is exactly those 3 deselected tests).
- `ruff check` / `ruff format --check` / `mypy src` / `vulture src`: all
  clean.
- Live-verified against the restored local Neon data: a *real* historical
  5.2% raise shows up at 2026-04 (`previous`, actual salary_base data), and
  the projected 2027-04 future increase now reads `0.86` (matching
  `(113.15/112.18 - 1) * 100` computed by hand), with every other month
  reading `0.0`.

### Not yet done

Same as prior sections: no commit, no push, awaiting explicit user
authorization.

## 13. USD/EUR/UF equivalents for non-future periods (2026-10-01)

User request: for every non-future period (`previous`/`current`) on
`GET /payroll/period-range`, expose how many US dollars, euros, and UF
`net_pay_clp` equates to on that period's own `start_date` (first day of
the period).

### Design

- **New DTO/Read fields**: `net_pay_usd`, `net_pay_eur`, `net_pay_uf` on
  both `PayrollPeriodRangeDTO` (`Decimal | None`) and
  `PayrollPeriodRangeRead` (`float | None`, plain JSON numbers -- same
  convention as `net_pay_clp`/`increase`). All three default to `None` so
  every existing DTO-construction call site across the codebase (tests
  included) keeps working unchanged.
- **New `domain/quantizers.py::quantize_currency_amount()`**: 2 decimals,
  the conventional display precision for USD/EUR/UF amounts -- a new,
  separate function from `quantize_percent()` even though both currently
  quantize to the same `0.01` constant, because they are semantically
  different concerns (a currency amount vs. a percentage) that happen to
  share a quantum today; collapsing them into one function would be a
  coincidence-driven abstraction, not a real one.
- **`pf-rates`' `get_exchange_rate_value(currency_code, date)`** is already
  fully generic -- the existing UF-only call sites (net_pay prediction, UF
  discount computation) never needed USD/EUR before, but the port and the
  HTTP client both already treat `currency_code` as an open string, so no
  interface change was needed there. Confirmed against the live local
  `pf-rates` service that it really does carry `USD`/`EUR`/`UF` (and
  `UTM`) data, not just `UF`, and that `value_clp` always means "CLP per 1
  unit of that currency/index" (already the implicit convention this
  codebase assumed for UF everywhere else -- e.g.
  `predict_next_period_net_pay()`'s `amount_clp / uf_rate` pattern), so
  `net_pay_clp / rate` is the correct, consistent conversion for all three.
- **New `payroll_repository_shared.py::resolve_currency_equivalents()`**
  (+ `CurrencyEquivalents` dataclass, `_convert_to_currency()` helper):
  given a `net_pay_clp` and a `rate_date`, concurrently resolves USD/EUR/UF
  via `asyncio.gather(..., return_exceptions=True)` and converts each
  independently. Deliberately **never raises** -- unlike
  `predict_next_period_net_pay()`/`project_future_months()` (which
  propagate `PayrollDependencyError` for `list_period_ranges()` to catch
  once), this is a 3-way-independent best-effort enrichment: one missing
  rate, one unpublished date, or pf-rates raising for one specific
  currency must never block the other two, so catching per-call via
  `return_exceptions=True` and treating any non-`Decimal` result
  (`None` or an exception object) as "no value" in one `isinstance` check
  is simpler and more correct here than threading a shared try/except
  around three independent lookups.
- **Call sites, deliberately excluding `future`**: `list_period_ranges()`
  calls `resolve_currency_equivalents()` once per *real* previous period
  (`previous_periods_ordered`, built once and reused both for the
  concurrent `asyncio.gather` and the final DTO-construction `zip`) and
  once for the current period, **not** for the lookback ghost (never
  emitted in the response -- would be 3 wasted pf-rates calls) and **not**
  for inferred/data-less previous periods (`net_pay_clp` is already `None`
  for those, so the function would short-circuit to all-`None` instantly
  anyway, but skipping the call outright still avoids the construction
  overhead). `future_ranges` never calls it at all: the user explicitly
  scoped this to non-future periods, and `future` entries' `net_pay_clp`
  is itself already only a projection -- converting a projection to
  foreign currency would stack two layers of approximation into one
  number with no way to tell them apart later.
- **Concurrency**: up to 13 non-future periods x 3 currencies = up to 39
  independent HTTP calls to `pf-rates` on a fully cold cache. All 39 run
  concurrently via nested `asyncio.gather` (one gather per period's 3
  currencies, all 13 of those gathers themselves gathered together) rather
  than sequentially -- `PfRatesClient` already has its own per-
  `(currency_code, date)` TTL cache (same cache instance used by the UF
  lookups elsewhere), so repeat requests for the same window are cheap
  regardless, but the very first cold request still benefits enormously
  from not serializing 39 round trips. Live-verified: ~1.65s cold,
  ~0.94s warm (same JSON output byte-for-byte both times), against the
  restored local Neon data + local `pf-rates`.
- **`completed_ranges`'s final re-wrap loop** (the step that recomputes
  every item's `end_date` from its successor's `start_date`) had to be
  updated too -- it reconstructs every `PayrollPeriodRangeDTO` field by
  field, so forgetting to carry `net_pay_usd`/`net_pay_eur`/`net_pay_uf`
  through there would have silently reset them all back to `None` right
  before the function returns (caught immediately by the new end-to-end
  repository test below, not by inspection -- a good reminder this
  specific "rebuild every field" step is the one place in this file most
  likely to silently drop a newly added DTO field).

### Tests

- `test_payroll_repository.py`'s local `FakeMarketDataRepository` (used across this whole test
  file) extended with an optional `rates_by_currency_date: dict[tuple[str,
  date], Decimal | None]` param, checked before falling back to its
  original UF-only `rates_by_date` behavior -- fully backward compatible
  with every existing test that doesn't pass it.
- 5 new unit tests directly against `resolve_currency_equivalents()`:
  happy path (all 3 convert correctly), `net_pay_clp=None` short-circuits
  without ever calling the repository (proven via a repository configured
  to raise on any call), no repository wired in at all, one currency
  unpublished for the date while the other two still succeed, and a full
  `PayrollDependencyError` outage degrading all three to `None` without
  propagating.
- 1 new end-to-end `list_period_ranges()` test
  (`test_list_period_ranges_converts_non_future_periods_to_foreign_currencies`)
  reusing the existing previous+current fixture, asserting both the
  previous and current period get real USD/EUR/UF values from their own
  `start_date`'s configured rate, and that the first future month -- which
  does get a real projected `net_pay_clp` in other tests, though not in
  this particular fixture -- never gets currency fields regardless.

### Validation

- `pytest`: 524 tests passing (3 pre-existing Docker-dependent tests still
  deselected, same root cause as every prior section), 100% coverage on
  every file this change touched (`interfaces/session.py` is exactly those
  3 deselected tests, unrelated).
- `ruff check` / `ruff format --check` / `mypy src` / `vulture src`: all
  clean.
- Live-verified against the restored local Neon data + local `pf-rates`:
  all 13 non-future periods in the window returned real, distinct
  USD/EUR/UF figures; all 12 future periods returned `null` for all three
  as designed; a repeat request returned byte-for-byte identical JSON
  roughly 43% faster (cache warm).

### Not yet done

Same as prior sections: no commit, no push, awaiting explicit user
authorization.

## 14. `PayrollPeriodRangeRead` field order (2026-10-01)

User requested a specific JSON key order for `GET /payroll/period-range`
items: `period_year, period_month, start_date, end_date, position,
net_pay_clp, net_pay_uf, net_pay_usd, net_pay_eur, increase`.

Plain dataclasses (and FastAPI/Pydantic's handling of them) serialize
fields in declaration order -- base class fields first
(`PayrollPeriodRangeFields`: `period_year`/`period_month`/`start_date`/
`end_date`, already in the right order and untouched), then the subclass's
own fields in whatever order they're declared. Reordered
`PayrollPeriodRangeRead`'s own fields from `net_pay_clp, position,
increase, net_pay_usd, net_pay_eur, net_pay_uf` to `position, net_pay_clp,
net_pay_uf, net_pay_usd, net_pay_eur, increase` to match exactly.

`increase` needed an explicit `= None` default added (it had none before
-- always passed explicitly) since Python dataclasses require every
no-default field to precede any defaulted ones, and it now sits after
three already-defaulted currency fields. Harmless: every construction call
site already passes every field explicitly by keyword, so this changes
nothing about how the dataclass is built, only what happens if a future
caller omits `increase` (defaults to `None`, same as the already-optional
currency fields). The single construction call site in
`to_payroll_period_range_reads()` was reordered to match too, purely for
readability -- keyword arguments mean this was never functionally
required.

**Validation**: full suite still 524 passing / 100% coverage on every
touched file, ruff/format/mypy clean. Live-verified against the restored
local Neon data: `list(data[0].keys())` now returns exactly
`['period_year', 'period_month', 'start_date', 'end_date', 'position',
'net_pay_clp', 'net_pay_uf', 'net_pay_usd', 'net_pay_eur', 'increase']`.

Not yet committed/pushed -- awaiting explicit user authorization, same as
every prior section.

## 15. N-month IPC extrapolation, implemented (2026-10-02)

Implements `docs/proposals/future-increase-ipc-extrapolation-design-
recommendation.md` in full. Summary of what actually landed (the
recommendation's own Sections 1-9 already cover the full rationale, not
repeated here):

- **`_extrapolate_cycle_ratio()`** (new, `payroll_repository_shared.py`):
  takes the raw M-month `real_ratio` and, when `missing_months =
  increase_frequency - months_elapsed` is positive, compounds a geometric
  monthly rate (derived from a trailing `increase_frequency`-month IPC
  window ending at `latest_period`) across those missing months. Falls
  back to `real_ratio` alone -- identical to pre-change behavior -- the
  moment there is nothing left to extrapolate (`missing_months <= 0`) or
  the trailing-window IPC figure isn't available/valid. Exactly matches
  the recommendation's Section 6 helper signature and Section 4's worked
  example numerically (`4.05%` / `3,244,420.33` CLP on the brief's own
  scenario).
- **`_apply_ipc_step()`**: gained the `increase_frequency` parameter
  (already available at its one call site, pure plumbing) and now computes
  `total_ratio` via the helper above instead of the bare M-month ratio
  inline; the deflation floor (`total_ratio < 1`) is gated on this
  consolidated ratio, not the raw M-month one (recommendation Section 5).
- **Tests** (`test_payroll_repository.py`): the existing worked-example
  test (`test_project_future_months_replicates_until_next_increase`) was
  updated with the recommendation's own trailing-anchor fixture value
  (IPC_CL 108.00 at 2025-08) so it now asserts the corrected `4.05%` /
  `3,244,420.33` instead of the previously-undercounted `0.86%` -- this is
  the brief's own motivating example, it had to reflect the fixed behavior.
  Two new tests close the coverage gap the implementation alone left open
  (lines 298-300 of `_extrapolate_cycle_ratio()`'s happy path were
  otherwise never exercised -- every pre-existing fixture happened to omit
  trailing-window data, which only proved the *fallback* branch, not the
  real extrapolation): one proves the `M >= N` cheap path never even
  attempts the trailing-window fetch (asserts `FakeMarketDataRepository`'s
  new call log, not just the resulting number), and one proves the
  deflation floor is gated on the consolidated ratio by constructing a case
  where the raw M-month ratio alone is mildly inflationary but the
  extrapolated ratio is not.
- **Result**: 101 tests in this file (up from 99), 526 passing overall,
  `payroll_repository_shared.py` back to 100% line *and* branch coverage
  (branch gaps at 190->182/223->227 are pre-existing, unrelated `for`-loop
  partials elsewhere in the file, not touched by this change). ruff
  check/format and mypy clean on both touched files (mypy's 21
  pre-existing `SimpleNamespace`/`FakeResult` typing warnings elsewhere in
  the test file are unchanged -- same count before and after, just at
  shifted line numbers -- out of scope for this change). Full-suite
  `--cov-fail-under=100` still fails only on `interfaces/session.py`
  (pre-existing, requires the Docker-backed integration tests that can't
  run in this environment -- unrelated to this change).

Not yet committed/pushed -- awaiting explicit user authorization, same as
every prior section.

## 16. Increase steps now scale salary_base, not net_pay (2026-10-02)

**User correction (2026-10-02):** after validating Section 15's
extrapolation against live data (employer 9, WALMART-CHILE), the user
flagged that an increase month's step was being applied to the whole
`net_pay_clp` figure instead of to the salary_base-driven portion, with
the usual per-payroll discount math reapplied from there. This was a real
bug, independent of the extrapolation ratio itself (which the user
confirmed was correct): `predict_next_period_net_pay()`'s first-month
prediction already decomposes into a salary_base-driven amount (gross net
of the period's own proportional non-UF discount ratio) and a UF-driven
amount (`HEALTH_ADDITIONAL_UF`, netted against the employer's UF-converted
health contribution) -- but `project_future_months()` only ever saw the
single already-combined `net_pay_clp` number, so every later increase step
multiplied *both* components by the same IPC-derived ratio. The UF-driven
component tracks the UF/CLP exchange rate, not a salary raise, so
multiplying it by the raise ratio silently mis-stated every future month's
`net_pay_clp` for any employer with a nonzero UF-indexed discount, by an
amount that grows with each subsequent step.

- **`PredictedNetPayBaseline`** (new, `payroll_repository_shared.py`):
  `predict_next_period_net_pay()` now returns this instead of a bare
  `Decimal`. `net_pay_clp` is unchanged (`scalable_clp - fixed_uf_clp`,
  both quantized to cents before subtracting so the identity holds exactly,
  no independent-rounding drift). `scalable_clp` is the salary_base-driven
  portion; `fixed_uf_clp` is `HEALTH_ADDITIONAL_UF`'s netted CLP amount.
- **`project_future_months()`**: takes `first_future_baseline:
  PredictedNetPayBaseline | None` instead of a bare Decimal. Tracks
  `current_scalable`/`fixed_uf_clp` separately; `_apply_ipc_step()` (now
  parameterized as `current_scalable`, same math otherwise) only ever
  scales the former. Every displayed month recombines
  `current_scalable - fixed_uf_clp` -- `fixed_uf_clp` itself never
  changes after month 1, there being no future UF forecast to recompute it
  against (same "nothing better available" reasoning as every other
  degrade-gracefully path in this subsystem).
- **Call site** (`payroll_repository_queries.py`): `first_future_baseline`
  threaded through `_project_future_months_or_degrade()` unchanged in
  spirit -- a `PayrollDependencyError` still degrades to
  `degraded_future_months(baseline.net_pay_clp if baseline else None)`,
  since the degraded path never steps anything regardless.
- **Tests**: `test_predict_next_period_net_pay_calculates_correctly` and
  `..._adjusts_for_worked_days` now assert `.net_pay_clp`/`.scalable_clp`/
  `.fixed_uf_clp` individually instead of comparing a bare Decimal. Every
  existing `project_future_months()` test (11 call sites) wraps its
  Decimal fixture through a new `_baseline()` test helper defaulting
  `fixed_uf_clp=0` -- i.e. `scalable_clp == net_pay_clp`, reproducing the
  pre-fix numeric behavior exactly for every test not specifically about
  this split. One new test,
  `test_project_future_months_does_not_scale_uf_driven_portion`, pins the
  actual fix: a baseline with `scalable_clp=1,000,000` /
  `fixed_uf_clp=100,000` (`net_pay_clp=900,000`) stepped by a real ratio of
  1.10 must land on `1,000,000` (`1,100,000 - 100,000`) at the next
  replicated month, not `990,000` (`900,000 * 1.10`) -- the number the
  pre-fix bug would have produced.
- **Result**: 102 tests in this file (up from 101), 527 passing overall,
  both touched production files back to 100% line coverage. ruff
  check/format clean; mypy clean on both production files (the test
  file's pre-existing 21 `SimpleNamespace`/`FakeResult` typing warnings are
  unchanged in count, just shifted line numbers -- out of scope here, same
  as Section 15). `docs/api.md`'s `/payroll/period-range` description
  updated in the same change to describe the split. Real-data impact: any
  employer with a nonzero `HEALTH_ADDITIONAL_UF` whose projection crosses
  an increase month will see a (small, correct) change in its projected
  `net_pay_clp` from this point forward; employers without that discount
  (the common case, and every pre-existing end-to-end test fixture) are
  numerically unaffected.

Not yet committed/pushed -- awaiting explicit user authorization, same as
every prior section.

## 17. `net_pay_clp_today`: reprice `previous` periods at today's UF rate (2026-10-02)

**User request (2026-10-02):** for `position: previous` entries, show what
that historical salary would be worth if paid today, using the UF it was
worth back then repriced at today's UF/CLP rate -- UF already strips out
CLP inflation by design, so this is a real purchasing-power comparison,
not another projection.

- **`_compute_net_pay_clp_today()`** (new, `routes/payroll.py`, alongside
  `_compute_increase()` -- same "derive purely from already-resolved DTO
  fields, no new repository call" pattern): today's UF/CLP rate is derived
  from the `current` entry alone -- `current.net_pay_clp /
  current.net_pay_uf` -- since both were already resolved against the
  exact same real `pf-rates` UF value for `current`'s own `start_date` (see
  `resolve_currency_equivalents()`). Multiplying a `previous` entry's own
  `net_pay_uf` by that derived rate needs no extra `pf-rates` lookup at
  all. Quantized with the same `quantize_clp()` (nearest peso) as
  `net_pay_clp` itself. `None` whenever any ingredient is missing: no
  `current` entry, either side's `net_pay_uf` unresolved, or
  `current.net_pay_clp`/`current.net_pay_uf` missing or zero.
- **`PayrollPeriodRangeRead`**: new `net_pay_clp_today: int | None = None`
  field. Initially placed right after `net_pay_uf`, then moved to the very
  end (after `increase`) per explicit user request -- it is a derived,
  comparison-only figure (not one of the core currency conversions grouped
  together earlier in the object), so it reads more naturally last.
- **`to_payroll_period_range_reads()`**: resolves the `current` DTO once
  (reusing the existing `current_index` lookup) and calls the helper only
  for `position == "previous"` rows; `current`/`future` always carry
  `null` -- `current` already *is* today's value, `future` is itself only
  a projection, so repricing either would be meaningless.
- **Tests** (`tests/integration/api/test_payroll_queries.py`): `_make_period_range()`
  gained a `net_pay_uf` kwarg; four new unit tests for
  `_compute_net_pay_clp_today()` (happy path using the user's own real
  worked example -- 76.29 UF from 2025-09 repriced at the 2026-09 current
  period's derived rate of ~41,048.95 CLP/UF lands on 3,131,625 CLP, not
  the original 3,012,409 nominal figure -- plus the three missing-
  ingredient degradations). The existing full-response JSON assertion in
  `test_payroll_query_endpoints` was extended with `net_pay_clp_today:
  None` on all three entries (none of that fixture's periods carry
  `net_pay_uf`).
- **Result**: 531 tests passing overall (up from 527), `routes/payroll.py`
  at 100% line coverage, ruff check/format and mypy clean. `docs/api.md`'s
  `/payroll/period-range` description updated in the same change.

Not yet committed/pushed -- awaiting explicit user authorization, same as
every prior section.

## 18. `net_pay_clp_today` v2: real salary_base ratio, not a blanket UF ratio (2026-10-02)

**User follow-up (2026-10-02):** Section 17's formula (`net_pay_uf *
today_uf_rate`) treats the *entire* historical `net_pay_clp` as if it were
UF-denominated, including the salary_base-driven majority of it -- the
same category of bug already fixed for `future` projections in Section 15
(there: scaling the whole net figure by the IPC ratio instead of only
`scalable_clp`). User asked for an efficient fix using the real
salary_base data already available, applying "all the calculations that
would be done to a salary" rather than a blanket currency conversion.

- **Repository** (`payroll_repository_queries.py`): the *existing* single
  batched query that already fetches `SALARY_BASE` sums for every
  previous/lookback/current period in one round trip (`period_ids IN
  (...)`) was extended with a second conditional aggregate --
  `SUM(amount_clp) FILTER (WHERE code = HEALTH_ADDITIONAL_CONCEPT_CODE)`
  -- so `fixed_uf_clp` comes along for free, same query, same round trip,
  no N+1. Missing/absent rows default to `Decimal("0")` (no additional
  Isapre plan that period == genuinely zero, not unknown -- same
  philosophy `MANDATORY_DECLARED_CONTRIBUTION_CONCEPT_CODES` already
  documents for this exact concept code). Kept as two single-statement
  dict comprehensions over one `.all()` call (not a `for` loop with
  statements inside it) specifically so coverage.py still marks both maps
  built even when the aggregate query legitimately returns zero rows --
  a `for ...: body` requires at least one iteration to cover `body`,
  a comprehension does not.
- **`PayrollPeriodRangeDTO`**: new `fixed_uf_clp: Decimal = Decimal("0")`
  field (never `None` -- absence means zero, not unknown), populated only
  for `previous_ranges` (the only place `_compute_net_pay_clp_today()`
  reads it from); `current`/`lookback` keep the dataclass default since
  nothing consumes it there.
- **`_compute_net_pay_clp_today()`** (`routes/payroll.py`), rewritten:
  splits `item`'s historical net_pay the same way `PredictedNetPayBaseline`
  splits a future prediction -- `scalable_clp = net_pay_clp +
  fixed_uf_clp`, `fixed_uf_clp` on its own -- and reprices each with its
  own real driver instead of one blanket ratio:
  - `scalable_clp` is scaled by the *real* salary_base growth between
    `item` and `current`, using the exact same `(salary_base /
    worked_days) * 30` normalization `_compute_increase()` already uses.
    This is this specific employee's actual recorded raise history, not a
    general inflation proxy.
  - `fixed_uf_clp` is repriced by holding its UF quantity constant
    (`fixed_uf_clp / item_uf_rate`) and applying today's UF/CLP rate --
    still derived algebraically from `net_pay_clp / net_pay_uf` on both
    sides, no extra `pf-rates` call, same trick as v1.
  - `None` whenever any ingredient is missing on either side:
    `net_pay_clp`, `net_pay_uf` (or zero), `salary_base`, `worked_days`, or
    a zero normalized historical salary_base (nothing to form a ratio
    against).
- **Live-verified against real Neon data** (employer 9, which has a real
  5.20% salary_base increase landing on 2026-04): every `previous` period
  *before* that increase now shows `net_pay_clp_today` repriced up by
  essentially that same 5.20% (e.g. 2025-09: 3,012,409 -> 3,169,054,
  previously 3,131,625 under the v1 UF-blanket formula); every `previous`
  period *after* that increase (same salary_base as `current`, ratio
  exactly 1.0) now correctly returns `net_pay_clp_today == net_pay_clp`
  unchanged, which v1 would have incorrectly bumped by whatever the UF
  happened to move in between for no real reason.
- **Tests**: `_make_period_range()` gained a `fixed_uf_clp` kwarg (default
  `Decimal("0")`, forwarded to the DTO). All `_compute_net_pay_clp_today()`
  unit tests rewritten with clean synthetic numbers exercising the full
  split (hand-verified: scalable_clp scaled 10% by a real salary_base
  ratio, a UF-denominated top-up repriced at a different UF rate,
  recombined) plus every degradation path (missing `current`, missing
  `net_pay_clp`/`net_pay_uf`/`salary_base`/`worked_days` on either side,
  zero normalized historical salary_base).
- **Result**: 533 tests passing, `routes/payroll.py` and
  `payroll_repository_queries.py` both at 100% line coverage, ruff
  check/format and mypy clean. `docs/api.md` updated in the same change to
  describe the split methodology instead of the v1 blanket UF ratio.

Not yet committed/pushed -- awaiting explicit user authorization, same as
every prior section.

