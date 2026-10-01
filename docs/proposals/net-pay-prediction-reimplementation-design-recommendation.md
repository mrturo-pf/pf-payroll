# Re-implement `predict_next_period_net_pay()` against pf-rates

> Unlike every other document in this folder, there is no user-written "design brief"
> behind this one -- it originates from investigating a real user-reported symptom
> (`GET /payroll/period-range`'s `future` periods always have `net_pay_clp: null`,
> even the very first one) during a live session. This document folds the brief
> (context, constraints) and the recommendation (concrete design) into one, grounded
> entirely in the real git history and the real pf-rates contract -- no guessing.

## 1. The symptom, and why it is not a bug

`GET /payroll/period-range` (`payroll_repository_queries.py::list_period_ranges()`)
already has code whose *only* purpose is to populate `net_pay_clp` for the first
`position: "future"` period (one month ahead of the detected current period):

```python
first_future_net_pay_clp: Decimal | None = None
if current_row is not None and not current_inferred:
    first_future_net_pay_clp = await predict_next_period_net_pay(
        self._session,
        current_period,
        date(current_year, current_month, 1),
        allow_provider_lookup=False,
    )
...
net_pay_clp=(first_future_net_pay_clp if month_offset == 1 else None),
```

The other 11 future periods are `None` by design -- there is no reasonable way to
project further out than one period (salaries change, raises happen, UF drifts). But
that one period is always `None` too in production today, because
`predict_next_period_net_pay()` itself (`payroll_repository_shared.py`) is a confirmed,
self-documented stub:

```python
async def predict_next_period_net_pay(
    session, current_period, current_period_end_month,
    fx_provider=None, allow_provider_lookup=True,
) -> Decimal | None:
    """Predict net_pay_clp for the next period.

    UF lookup moved to pf-rates; returns None until the feature
    is re-implemented using the HTTP client.
    """
    del session, current_period, current_period_end_month, fx_provider, allow_provider_lookup
    return None
```

### How it got this way (confirmed via `git log --all`)

| Commit | What happened |
| --- | --- |
| `23ac707` | Feature originally shipped: predicts `net_pay_clp` for the first future period using a local `ExchangeRateModel` table + an injected `FxRateProvider` port (pf-payroll owned its own market-data tables and provider chain back then). |
| `bdc3d29` | A correctness fix to the UF-driven prediction (the version this document reimplements against). |
| `25e38f5` | **"migrate pf-payroll to consume pf-rates HTTP API for financial data."** Removed `ExchangeRateModel`, `EconomicIndexModel`, the `rate_provider` port, and every local provider implementation outright -- `MarketDataRepository` (a `Protocol`) + `PfRatesClient` (an HTTP adapter) replaced them everywhere **except** inside `predict_next_period_net_pay()`, which was stubbed to `return None` instead of being ported. Every other market-data consumer in the codebase (`ComplementaryInsuranceCostComputationService`, `ComputeIncomeTax`, `ComputeContributions`, `DeflateAmounts`, `ProcessImportedPayrollPeriods`) was migrated in that same commit; this one function was missed. |

Leftover unit tests (`test_predict_next_period_net_pay_calculates_correctly()` and
siblings, still present in `tests/unit/infrastructure/test_payroll_repository.py`) still
pass today -- but only because the stub returns `None` unconditionally, which happens to
satisfy their `assert result is None` variants; the ones asserting a real computed
`Decimal` were effectively neutered (they still *call* the function, but the function no
longer reads any of the UF-shaped fixtures they set up). This is the exact algorithm this
document restores, verbatim, re-pointed at pf-rates.

## 2. The original algorithm (recovered from `git show bdc3d29:...`)

Given the current payroll period's items and the UF value for a target month:

1. Resolve `uf_current`: the UF value for the **last day of the current period's own
   month** (via `current_period_end_month`, which the caller already passes as
   `date(current_year, current_month, 1)` -- i.e. always near-term, never more than
   ~1 month out from "today"). Bail out (`return None`) if unavailable.
2. Sum the current period's `PayrollItemModel` rows into four buckets by `concept_code`:
   - **Income** (`SALARY_BASE`, `LEGAL_GRATUITY`, `TELEWORK_REFUND`) -- assumed to repeat
     unchanged next period (next period's `future_gross` starts equal to this).
   - **Non-UF discounts** (`PENSION_BASE`, `PENSION_ADDITIONAL`, `HEALTH_BASE`,
     `HEALTH_INSURANCE`, `UNEMPLOYMENT_INSURANCE`, `INCOME_TAX`) -- assumed to scale with
     gross income (ratio-projected, not copied verbatim).
   - **UF discounts** (`HEALTH_ADDITIONAL_UF`) -- re-priced using the UF delta, not
     ratio-projected (a UF-denominated plan cost changes with the UF, not with income).
   - `HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION` -- also UF-denominated, re-priced the same
     way, and (being an employer-paid addition to gross, not a discount) added into both
     `current_gross`/`future_gross`.
3. If there is no income (`future_gross <= 0` or `current_gross <= 0`), bail out -- a
   period with no `SALARY_BASE` et al. is not something this can meaningfully project.
4. Resolve a **second**, independent UF reference (`reference_uf_for_current`), used
   purely to convert the *current* period's already-known UF-priced CLP amounts back
   into a UF quantity before re-pricing them at `uf_current`: exact UF at
   `current_period.payment_date` (already in the past -- never a forward-looking
   lookup). Only resolved at all if there's a nonzero UF-discount or employer
   health-insurance-contribution amount to convert; skipped entirely otherwise.
5. Convert each UF-denominated CLP amount -> UF quantity (divide by
   `reference_uf_for_current`) -> re-priced CLP (multiply by `uf_current`).
6. If `worked_days < 30` (a partial-month period), scale both `current_gross` and
   `future_gross` up to a full 30-day accounting month before computing the non-UF
   discount ratio -- a partial period's discount-to-income ratio is still representative
   of a full month's ratio, but the raw totals are not.
7. `future_non_uf_discounts = future_gross * (current_non_uf_discounts / current_gross)`;
   `future_discounts = future_non_uf_discounts + future_uf_discounts`;
   `predicted_net_pay = future_gross - future_discounts`.
8. Bail out (`return None`) if the result would be `<= 0` -- a negative/zero prediction
   is a signal something upstream is wrong, not a number worth showing a human.
9. Quantize to `Decimal("0.01")` (CLP cents) and return.

**Worked example**, lifted directly from the pre-stub test
(`test_predict_next_period_net_pay_calculates_correctly`, commit `bdc3d29`):

| Input | Value |
| --- | --- |
| `SALARY_BASE` + `LEGAL_GRATUITY` + `TELEWORK_REFUND` | 3,000,000 + 200,000 + 100,000 = 3,300,000 |
| `HEALTH_ADDITIONAL_UF` (UF-priced discount) | 35,000 CLP |
| `HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION` (UF-priced addition) | 8,000 CLP |
| `PENSION_BASE` + `HEALTH_BASE` + `HEALTH_INSURANCE` + `INCOME_TAX` (non-UF discounts) | 100,000 + 50,000 + 30,000 + 50,000 = 230,000 |
| `reference_uf_for_current` (UF at `current_period.payment_date`) | 40,000.00 |
| `uf_current` (UF at end of current period's month -- the "future" reference) | 40,821.18 |

```text
employer_uf_quantity            = 8,000 / 40,000.00            = 0.2
future_employer_contribution    = 0.2 * 40,821.18              = 8,164.236
future_health_additional_uf     = (35,000 / 40,000.00) * 40,821.18 = 35,718.5325
expected_gross                  = 3,300,000 + 8,164.236         = 3,308,164.236
expected_discount_ratio         = 230,000 / 3,308,000           = 0.06954...
expected_discounts              = expected_gross * ratio + future_health_additional_uf
expected_net_pay                = expected_gross - expected_discounts
                                 => quantized to 2dp
```

This document proposes restoring exactly this algorithm, with only the UF-resolution
plumbing (step 1 and step 4) re-pointed at pf-rates.

## 3. What pf-rates already gives us for free (confirmed from its own source)

The old implementation's UF resolution (`_resolve_uf_value()`, since deleted) was a
hand-rolled four-tier cascade: exact DB row -> live provider for the target date ->
live provider for *today* -> latest DB row before the target date. Checking pf-rates'
own `GetExchangeRateValue` use case
(`pf-rates/src/rates/application/use_cases/get_exchange_rate_value.py`), its resolution
order -- already live behind `GET /exchange-rates/value`, which `PfRatesClient.
get_exchange_rate_value()` already calls -- is:

1. Exact date in pf-rates' own database.
2. Exact date from pf-rates' external provider chain (result persisted for next time).
3. Nearest prior date in pf-rates' database (steps 3/4 only for past/today dates).
4. Provider probed day-by-day backwards up to `MAX_PROVIDER_LOOKBACK_DAYS`.
5. Not found -> pf-rates itself raises `ExchangeRateNotFoundError` -> pf-payroll's
   `PfRatesClient` sees that as an HTTP 404 -> `get_exchange_rate_value()` returns
   `None` (confirmed in `pf_rates_client.py`: `value = Decimal(...) if result is not
   None else None`, and `pf_rates_get()` returns `None` on 404).

**This cascade is a strict superset of the old local one** -- the entire four-tier
fallback this function used to hand-roll against its own `ExchangeRateModel` table now
happens transparently, server-side, inside pf-rates, on every `get_exchange_rate_value()`
call. Re-implementing this function needs **zero custom fallback logic** of its own
anymore: one `await self._market_data_repository.get_exchange_rate_value("UF", target_date)`
call per UF reference needed (two, in the worst case: `uf_current` and, conditionally,
`reference_uf_for_current`), full stop.

**Confirmed safe to call synchronously from a `GET` route:** `GET /exchange-rates/value`
is a pure read (possibly triggering a live provider fetch on a cache miss, server-side,
inside pf-rates -- never a destructive write visible to pf-payroll). This exact endpoint
is already called synchronously, today, in production, from
`ComplementaryInsuranceCostComputationService.compute()` (for `HEALTH_ADDITIONAL_UF`
plan costs) -- this reimplementation introduces no new category of runtime risk, just a
second call site for a pattern this codebase already trusts.

**No pf-rates infrastructure changes needed.** `uf_current`'s target date is always
`date(current_year, current_month, 1)`'s last day -- i.e. the **current** period's own
month, never more than a few weeks out from "today". pf-rates' own `/sync` job keeps a
rolling `forward_days: 35` window populated by default (see `pf-rates/docs/api.md`'s
`/sync` section: *"UF includes pre-published future values (Banco Central publishes UF
up to 3 months ahead)"*), comfortably covering this date in steady state. No change to
pf-rates' sync window, no new endpoint, no new table -- this is purely a pf-payroll-side
fix. **Cost impact: zero new infrastructure**, a handful of extra cached HTTP GETs to a
service pf-payroll already depends on and already pays for.

## 4. One real risk this reimplementation must not ignore

`pf_rates_get()` (`infrastructure/http/_http_client.py`) raises `PayrollDependencyError`
on **any** non-404 HTTP error or network failure (pf-rates down, timeout, 500, etc.) --
by design, so a caller like `ComputeIncomeTax` (where UF/UTM data is *required* to
compute anything at all) fails loudly rather than silently producing a wrong tax figure.

`GET /payroll/period-range` is different: the predicted `net_pay_clp` for the first
future period is a nice-to-have enhancement over an otherwise fully DB-only, always-
available endpoint (every other field already works with zero external dependencies).
**If `predict_next_period_net_pay()` lets a `PayrollDependencyError` propagate
uncaught, a pf-rates outage would turn the entire period-range response into a 500 --
breaking 24 periods of real data over one optional field on one of them.** This endpoint
must degrade that one field to `null` instead, exactly like "UF data not found" already
does today.

**Recommendation:** wrap the `predict_next_period_net_pay()` call site in
`list_period_ranges()` in a `try/except PayrollDependencyError: first_future_net_pay_clp
= None` (one boundary, not scattered `try` blocks inside the prediction function
itself) -- the only new piece of defensive code this reimplementation needs beyond
porting the original algorithm.

## 5. Dependency-injection plumbing

`predict_next_period_net_pay()` is called from
`SqlAlchemyPayrollQueryRepository.list_period_ranges()`, which only has `self._session`
available today (`SqlAlchemyPayrollRepositoryBase.__init__(self, session)`). The live
`MarketDataRepository` (`PfRatesClient`) is currently constructed ad hoc, per-use-case,
via `get_market_data_repository()` in `interfaces/api/dependencies.py` -- `PayrollQueries`
(the use case wrapping this repository) never sees it today.

**Proposed: add `market_data_repository: MarketDataRepository | None = None` to
`SqlAlchemyPayrollRepositoryBase.__init__`**, stored as `self._market_data_repository`.

- **Why the shared base, not just `SqlAlchemyPayrollQueryRepository`:** the public
  facade (`SqlAlchemyPayrollRepository`) composes `Imports`/`Commands`/`Queries` via
  multiple inheritance. **Confirmed experimentally while implementing this** (not just
  by reading): `SqlAlchemyPayrollImportRepository` (the first mixin in the facade's base
  list) already overrides `__init__` itself, to also build a nested
  `SqlAlchemyReferenceDataRepository` -- so Python's MRO resolves `__init__` to *that*
  override, never falling through to the shared base at all. Adding the parameter only
  to `SqlAlchemyPayrollQueryRepository` (relying on MRO to route it there) would have
  been not just fragile but **outright broken** -- the facade would have silently
  dropped the argument via `Imports.__init__(self, session)`'s fixed signature
  (confirmed via a `TypeError: ... takes 2 positional arguments but 3 were given` on
  the first real test run). The fix: `SqlAlchemyPayrollImportRepository.__init__` also
  needed its own explicit forward of the new parameter into `super().__init__(...)` --
  one extra, deliberate touchpoint beyond the shared base, not an MRO-abuse shortcut.
  `SqlAlchemyPayrollCommandRepository` has no `__init__` override of its own, so it
  correctly inherits whichever `__init__` MRO resolves to above it.
- **Why optional/defaulted to `None`, not required:** this keeps the change
  fully backward compatible. Every existing test or call site that constructs
  `SqlAlchemyPayrollRepository(session)`/`SqlAlchemyPayrollQueryRepository(session)`
  directly (there are dozens across the test suite) keeps working completely unchanged
  -- `predict_next_period_net_pay(market_data_repository=None, ...)` simply short-circuits
  to `None`, which is *exactly* today's stub behavior. Only
  `get_payroll_repository()`/`get_transactional_payroll_repository()` (the two FastAPI DI
  factories) need one line added each, passing `get_market_data_repository()` through,
  to make the feature live in production.
- **Port (`application/ports/repositories.py`) and application layer
  (`PayrollQueries`) stay untouched.** `PayrollRepository.list_period_ranges(today=...)
  -> list[PayrollPeriodRangeDTO]` is unchanged -- which market data the concrete adapter
  reaches for internally is an infrastructure-layer implementation detail, invisible to
  the port's contract. This is the cleanest-possible diff for a hexagonal codebase: no
  new port method, no change to any use case, no new constructor argument threaded
  through a use case class.

## 6. Reimplementation shape (function signature)

```python
async def predict_next_period_net_pay(
    session: AsyncSession,
    current_period: PayrollPeriodModel,
    current_period_end_month: date,
    market_data_repository: MarketDataRepository | None = None,
) -> Decimal | None:
    """Predict net_pay_clp for the next period based on current period data.
    ... (same docstring as the original -- algorithm is unchanged) ...
    """
    if market_data_repository is None:
        return None
    last_day_current_month = get_last_day_of_month(current_period_end_month)
    uf_current = await market_data_repository.get_exchange_rate_value(
        "UF", last_day_current_month
    )
    if uf_current is None or uf_current <= 0:
        return None
    # ... (items query + bucketing: unchanged from the original) ...
    reference_uf_for_current: Decimal | None = uf_current
    if current_uf_discounts > 0 or employer_health_insurance_clp > 0:
        reference_uf_for_current = await market_data_repository.get_exchange_rate_value(
            "UF", current_period.payment_date
        )
    if reference_uf_for_current is None or reference_uf_for_current <= 0:
        return None
    # ... (re-pricing, proration, ratio projection, quantize: unchanged) ...
```

`fx_provider`/`allow_provider_lookup` are dropped entirely (the concepts they named --
a local provider chain, and a flag to skip it -- no longer exist anywhere in
pf-payroll); `_resolve_uf_value()` and `_build_fx_provider()` are deleted outright
(dead code -- their entire reason to exist was superseded by pf-rates owning that
cascade now). `del (...)` at the top of the stub body is removed along with the stub
itself.

**Call site** in `list_period_ranges()`:

```python
first_future_net_pay_clp: Decimal | None = None
if current_row is not None and not current_inferred:
    try:
        first_future_net_pay_clp = await predict_next_period_net_pay(
            self._session,
            current_period,
            date(current_year, current_month, 1),
            self._market_data_repository,
        )
    except PayrollDependencyError:
        first_future_net_pay_clp = None
```

## 7. Scope explicitly NOT touched

- **Only the first future period (`month_offset == 1`) gets a prediction** -- unchanged
  from the original design. The other 11 stay `null`; there is no UF-driven way to
  project two+ months out, and attempting one is out of scope for restoring a feature
  that already existed.
- **No change to `docs/api.md` or the Postman collection.** `GET /payroll/period-range`
  already documents that `future` periods carry `net_pay_clp` (implicitly `null` today);
  this reimplementation makes that field *populate* for one row under normal operation,
  it does not change the response shape, add a field, or change any request parameter.
  Confirmed by re-reading `docs/api.md`'s existing row for this endpoint -- it never
  claimed `future` entries are always `null`, so there is no stale claim to fix either.
- **No change to `position`/`increase` semantics**, `previous`/`current` periods, or
  the 12/12 window sizing -- this touches exactly one field, on exactly one row, of
  one existing endpoint.

## 8. Action plan

1. **`payroll_repository_shared.py`**: restore `predict_next_period_net_pay()`'s real
   body (Section 6), re-pointed at `MarketDataRepository`; delete `_resolve_uf_value()`
   and `_build_fx_provider()` (dead code, nothing else calls them); remove the now-unused
   `FxRateProvider` import (confirm nothing else in the file still needs it).
2. **`SqlAlchemyPayrollRepositoryBase.__init__`**: add the optional
   `market_data_repository` parameter (Section 5).
3. **`payroll_repository_queries.py`**: update the `list_period_ranges()` call site to
   pass `self._market_data_repository` instead of `allow_provider_lookup=False`, wrapped
   in the `try/except PayrollDependencyError` boundary (Section 4).
4. **`interfaces/api/dependencies.py`**: pass `get_market_data_repository()` into both
   `get_payroll_repository()` and `get_transactional_payroll_repository()` (the second
   one only for consistency/future-proofing -- `list_period_ranges()` is a read-only
   query never reached through the transactional scope today, but keeping both
   factories' behavior aligned avoids a silent divergence later).
5. **Tests**:
   - Rewrite the stale `predict_next_period_net_pay` unit tests
     (`test_predict_next_period_net_pay_calculates_correctly`,
     `..._uses_historical_uf_fallback`, `..._adjusts_for_worked_days`,
     `..._returns_none_zero_net_pay`, `..._returns_none_for_missing_uf`,
     `..._returns_none_for_missing_income`, `..._uf_fallback_cascade`,
     `..._db_only_mode`) against the new signature (a fake `MarketDataRepository`
     double instead of `FakeFxRateProvider` + raw `ExchangeRateModel` query mocking).
     The historical-fallback/cascade-specific tests collapse into fewer cases now that
     pf-rates owns that cascade -- the only client-side branching left is "value present"
     vs. "value absent" per call.
   - Add a case for `market_data_repository=None` -> `None` (today's default/back-compat
     behavior for any caller that doesn't wire one in).
   - Add a case where `market_data_repository.get_exchange_rate_value()` raises
     `PayrollDependencyError` -> caught at the `list_period_ranges()` boundary, that one
     field is `None`, the rest of the response is unaffected (**the one genuinely new
     test this reimplementation needs that the original feature never had**, since the
     failure mode itself is new -- an HTTP dependency that didn't exist before).
   - Extend the existing `GET /payroll/period-range` integration test(s) with a fake
     `MarketDataRepository` returning known UF values, asserting the first future
     period's `net_pay_clp` is populated and matches a hand-computed expectation (reusing
     Section 2's worked example numbers).
6. **Full quality gate**: `make test-cov` (100% coverage maintained), `ruff
   check`/`ruff format --check`/`mypy`/`vulture`/`jscpd` all clean, same bar as every
   other change in this repo.

No `pf-db` migration, no `pf-rates` change, no new endpoint, no `docs/api.md`/Postman
update -- this is a pure pf-payroll bugfix restoring previously-shipped, previously-
tested behavior that a prior migration accidentally dropped.

## 9. Implementation results (2026-10-01)

All of Section 8's steps were carried out exactly as planned, with one correction to
Section 5 (documented inline there, not repeated here): `SqlAlchemyPayrollImportRepository`
turned out to already override `__init__` itself, so it also needed an explicit forward
of `market_data_repository` -- caught immediately by the test suite
(`TypeError: ... takes 2 positional arguments but 3 were given` on the very first
`get_transactional_payroll_repository()` test run), not by inspection. No other surprises.

- `predict_next_period_net_pay()` restored verbatim per Section 6/the worked example in
  Section 2, in `payroll_repository_shared.py`; `_resolve_uf_value()`/`_build_fx_provider()`
  did not need explicit deletion -- they no longer existed in this file (already removed
  by the `25e38f5` migration alongside the rest of the local FX infrastructure; only the
  stub itself remained to be replaced).
- `SqlAlchemyPayrollRepositoryBase.__init__` and `SqlAlchemyPayrollImportRepository.__init__`
  both gained the optional `market_data_repository: MarketDataRepository | None = None`
  parameter, forwarded correctly end to end.
- `list_period_ranges()`'s call site updated to pass `self._market_data_repository` and
  wrap the call in `try/except PayrollDependencyError: first_future_net_pay_clp = None`.
- `get_payroll_repository()` and `get_transactional_payroll_repository()`
  (`interfaces/api/dependencies.py`) both now pass `get_market_data_repository()` through.
- Tests: replaced `FakeFxRateProvider` with `FakeMarketDataRepository` (mirrors
  `MarketDataRepository`'s actual two-method shape, plus an optional `raises=` mode for
  simulating a pf-rates outage). Rewrote all the stale `predict_next_period_net_pay` unit
  tests against the new signature; added the two genuinely new cases Section 8 called
  for -- `market_data_repository=None` and a `PayrollDependencyError` propagating out of
  the function uncaught (confirming the catch boundary lives in `list_period_ranges()`,
  not inside the prediction function itself) -- plus one new `list_period_ranges()`-level
  integration case proving the whole endpoint degrades gracefully (`result[13].net_pay_clp
  is None`) rather than raising, under a simulated pf-rates outage. A near-duplicate
  between that new test and the pre-existing
  `test_sqlalchemy_payroll_repository_infers_current_month_offset()` (both build the same
  id=19/June-2026 current-period `FakeSession` fixture) was caught by `make
  duplicate-code-tests` and fixed by extracting `_build_current_period_fixture_session()`
  -- not by suppressing the check.
- Final state: **504 tests passing (500 beforehand) -- removed the obsolete
  `..._stub_returns_none` test, added 5 new cases (missing-repository,
  dependency-error-propagates, the restored calculates-correctly and
  adjusts-for-worked-days happy-path cases, and the new list_period_ranges-level
  outage-degrades-gracefully case), net +4**, **100% coverage maintained**, `ruff
  check`/`ruff format --check`/`mypy`/`vulture`/`make duplicate-code-src`/`make
  duplicate-code-tests` all clean.
- Not yet done (requires explicit user authorization per this repo's `AGENTS.md`): no
  commit, no push. Awaiting the user's go-ahead.
- **One planned step turned out to be unnecessary, by design, not by oversight:**
  Section 8 proposed also extending the `GET /payroll/period-range`
  *integration* tests (`tests/integration/api/test_payroll_queries.py`). On inspection,
  every one of those tests overrides the FastAPI dependency with a hand-built fake
  `PayrollRepository` whose `list_period_ranges()` returns canned `PayrollPeriodRangeDTO`
  objects directly -- they exercise the HTTP route/response-shape layer only, never the
  real `SqlAlchemyPayrollQueryRepository`. The actual UF-driven prediction logic lives
  entirely below that boundary, so the repository-level tests already added in
  `test_payroll_repository.py` are the correct and sufficient place to verify it; adding
  a redundant integration-level double would only re-test route plumbing that was never
  in question. No integration test changes were made, and none are needed.
