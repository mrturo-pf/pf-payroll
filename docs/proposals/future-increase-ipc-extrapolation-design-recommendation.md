## 0. Scope

This recommendation answers
`docs/proposals/future-increase-ipc-extrapolation-design-brief.md` in full: it
resolves the brief's three flagged blind spots, answers all 7 "What I need"
items, and ends with a concrete implementation plan. No code has been changed
yet -- this is the design only, same two-step process as every other proposal
in this folder.

## 1. The exact gap, restated precisely

`_apply_ipc_step()` (`payroll/infrastructure/db/repositories/payroll_repository_shared.py`,
lines 247-312) computes exactly one ratio today:

```python
increase_pct = quantize_percent((latest_value / last_increase_index - 1) * 100)
stepped = (current_value * latest_value / last_increase_index).quantize(_NET_PAY_QUANT)
```

`last_increase_index` is the IPC at `last_increase_period` (the anchor -- the
last real/scheduled increase month). `latest_value`/`latest_period` is
whichever IPC figure `pf-rates` has most recently published
(`get_latest_economic_index()`). The number of months between those two dates
-- call it **M** -- is never computed or considered anywhere in this function.
The employer's actual configured cadence -- `PAY_EMPLOYER.increase_frequency`,
**N** months -- is available at the call site
(`project_future_months()` already receives it as a parameter) but is never
passed into `_apply_ipc_step()` at all today.

Net effect: whatever fraction of the N-month cycle happens to already be
covered by published IPC data (M months) is reported and applied as if it
were the full cycle's adjustment. The brief's worked example (M=4, N=12) is
real test data, not a hypothetical: `test_project_future_months_replicates_until_next_increase`.

## 2. Resolving the three blind spots from the brief

### 2.1 `NULL PAY_EMPLOYER` -- confirmed already handled, no new work needed

`SqlAlchemyPayrollQueryRepository._resolve_increase_frequency()`
(`payroll_repository_queries.py`) defaults a `NULL increase_frequency` to 12;
`_resolve_first_increase_period()` defaults a `NULL`
`(first_increase_period_year, first_increase_period_month)` pair to
`employer_started_at` plus that same resolved frequency. Both already run
*before* `project_future_months()` is ever called
(`list_period_ranges()`), so `increase_frequency` (N) reaching
`_apply_ipc_step()` is **always** a concrete positive int by the time this
methodology would run -- no new default rule needed, confirmed.

### 2.2 The `N - M <= 0` guard

**Resolution: when `M >= N`, skip extrapolation entirely and use the real
M-month ratio as-is, with no new IPC fetch at all.** This is not just the
simplest option -- it is the *correct* one: if M already covers (or exceeds)
a full configured cycle, the real, published data already represents the
whole adjustment (or more than it, in the "contractually late" case the brief
describes). There is nothing left to estimate, and guessing an additional
projection on top of already-complete real data would double-count months
that already happened. This also means `M >= N` is the cheap path: it never
triggers the new `get_economic_index_value()` lookup described in 2.4 below,
so a late-adjustment employer costs nothing extra over today's behavior.

`M <= 0` (last_increase_period at or after latest_period -- possible in
principle if IPC publication lags behind an employer's anchor date, though
not observed in practice) is folded into the *same* guard
(`M >= N` is checked second; `M <= 0` makes the "months already covered"
window trivially non-positive, so falling back to the real ratio -- which in
this case is computed from whatever two data points exist regardless -- is
the same safe, no-worse-than-today behavior). No separate code path needed.

### 2.3 Arithmetic vs. compound (geometric) mean -- **recommend geometric**

Worked numerically in Section 4 below. The two reasons to prefer geometric
over arithmetic:

1. **Internal consistency.** `real_ratio` (`latest_value / last_increase_index`)
   is itself inherently a compounded ratio, not a sum of monthly deltas, and
   `_apply_ipc_step()` already composes ratios multiplicatively everywhere
   else (`current_value * latest_value / last_increase_index` -- a product,
   never a sum). Mixing an arithmetically-averaged monthly rate into an
   otherwise fully multiplicative pipeline would be the one inconsistent
   step in the whole chain. The geometric monthly rate
   `r_m = (1 + r_N)^(1/N) - 1` is defined precisely so that
   `(1 + r_m)^N == 1 + r_N` exactly -- it *is* the rate that reproduces the
   real observed N-month trailing change when compounded, which an
   arithmetic average does not guarantee.
2. **It costs nothing extra to implement.** Python's `decimal.Decimal`
   supports fractional exponents natively via `Decimal.__pow__` (correctly
   rounded per the General Decimal Arithmetic spec) -- confirmed by hand
   against the default 28-digit context this codebase already relies on
   everywhere (no custom `decimal.Context` is set anywhere in `pf-payroll`):

   ```pycon
   >>> from decimal import Decimal
   >>> trailing_ratio = Decimal("113.15") / Decimal("108.00")
   >>> trailing_ratio ** (Decimal(1) / Decimal(12))
   Decimal('1.003889473190846019390921468')
   ```

   No `float` conversion, no `math.pow`, no new dependency. `Decimal ** int`
   (for raising the monthly ratio to the `N - M` missing-months power) is
   ordinary repeated multiplication, already used throughout this file.

For the realistic magnitudes involved (Chilean monthly inflation, roughly
0.3-0.8%), the numeric difference between the two approaches turns out to be
small (see Section 4: 4.04% vs. 4.05% in the worked example) -- but it is not
zero, it is the mathematically correct choice for compounding, and it is free
to implement, so there is no reason to settle for the arithmetic
approximation.

### 2.4 New data-fetch plan

**No new port method needed.** `MarketDataRepository.get_economic_index_value(code,
year, month)` (`application/ports/repositories.py`) already accepts an
arbitrary `(year, month)`, which is exactly what's needed to fetch
`IPC_CL` at `period_back = add_months(latest_period, -N)` (2025-08 in the
worked example, paired with 2026-08 for a trailing 12-month window --
see Section 3 for why this specific window definition was chosen). This is
simply a third call to an already-existing, already-used method, with a
different `(year, month)` argument than either of the two calls
`_apply_ipc_step()` already makes.

**Degradation when `period_back`'s IPC is missing or `<= 0`** (index not
published that far back yet, or a gap in `pf-rates`' data): **fall back to
`real_ratio` alone -- i.e., exactly today's pre-this-change behavior.** This
is the least surprising choice and requires no new concept: every existing
guard in `_apply_ipc_step()` already degrades to "the best answer computable
from the data actually available" rather than giving up outright (see its
own docstring: missing anchor, missing latest IPC, zero/negative baseline,
and the deflation floor all already follow this philosophy). Falling back to
`real_ratio` here is the same pattern applied to the one new failure mode
this adds, not a new philosophy.

## 3. Pinning down the N-month window (What I need, item 1)

**Resolution: a trailing window of N calendar months *ending at `latest_period`*
(`period_back = add_months(latest_period, -N)`), not anchored at
`last_increase_period`.** Two reasons:

- It answers "what has inflation looked like *recently*", which is the most
  defensible proxy for "what is it likely to keep looking like for the
  remaining N-M months" -- a trend window anchored to the (potentially much
  older) `last_increase_period` would mix in months that are less relevant to
  projecting the immediate future.
- It is independent of M, so the exact same `period_back` computation and IPC
  fetch would be reused even across employers/cycles with wildly different
  `last_increase_period` dates, as long as `latest_period` (a single
  process-wide "what does pf-rates know right now" fact) is the same -- in
  fact, `PfRatesClient`'s own per-`(currency_code, date)`/`(code, year,
  month)` TTL cache (confirmed in `infrastructure/http/pf_rates_client.py`,
  already relied on by every other market-data lookup in this codebase) means
  this specific `period_back` lookup is cache-shared across every employer's
  `_apply_ipc_step()` call within the same cache TTL window, not refetched
  per employer.

This defines "the last N published months" as an N-month *gap* (two data
points, N months apart), not N individual data points -- consistent with how
`increase_frequency`/`is_increase_period()`/`resolve_last_increase_period()`
already treat "N months" everywhere else in this codebase (a month-index
delta, never a count of rows).

## 4. Worked example, end to end

Using the brief's own scenario: `last_increase_period = 2026-04`
(`last_increase_index = 112.18`), `latest_period = 2026-08`
(`latest_value = 113.15`), `N = 12`. **`IPC_CL` for 2025-08 does not exist in
any fixture today** (the current test suite only ever defines the two data
points above) -- for this worked example an illustrative value of **108.00**
is assumed for 2025-08 (a plausible ~4.8% trailing annual figure for Chile);
whoever implements this must seed a real third fixture value, this is not a
claim about real published data.

| Step | Computation | Value |
|---|---|---|
| M (months elapsed) | `(2026*12+8) - (2026*12+4)` | **4** |
| N - M (missing months) | `12 - 4` | **8** (`> 0` → extrapolate) |
| `real_ratio` | `113.15 / 112.18` | `1.008646817614548047780353004` |
| `period_back` | `add_months(2026-08, -12)` | `2025-08` |
| `trailing_ratio` (N-month) | `113.15 / 108.00` (assumed) | `1.047685185185185185185185185` |
| `r_m` geometric monthly | `trailing_ratio ** (1/12)` | `1.003889473190846019390921468` |
| `r_m` arithmetic monthly | `(trailing_ratio - 1) / 12` | `1.003973765432...` *(as a 1+rate, for comparison)* |
| `(r_m geometric) ** 8` | | `1.031542680681922623792257769` |
| **`total_ratio` (geometric, recommended)** | `real_ratio * (r_m_geom ** 8)` | **`1.040462242103401184543536874`** |
| **`increase_pct` (geometric)** | `(total_ratio - 1) * 100`, quantized | **`4.05`** |
| `total_ratio` (arithmetic, for comparison) | `real_ratio + (r_m_arith - 1) * 8` | `1.040436941071338171237127...` |
| `increase_pct` (arithmetic, for comparison) | | `4.04` |
| `increase_pct` (today's unfixed behavior) | `(real_ratio - 1) * 100` | `0.86` |

Using `current_value = 3118248.98` (the exact figure already used by
`test_project_future_months_replicates_until_next_increase`):

| Method | Stepped `net_pay_clp` |
|---|---|
| Today (M-only, unfixed) | `3,145,211.91` |
| New, arithmetic | `3,244,341.43` |
| **New, geometric (recommended)** | **`3,244,420.33`** |

The gap between "today" and "fixed" is **~99,000 CLP** on this one worked
example -- this is the real-world magnitude of the bug the brief describes,
not a rounding nuance. The gap between arithmetic and geometric themselves is
~79 CLP / 0.01 percentage points -- real, but secondary to fixing the much
larger M-vs-N gap in the first place.

**Guard case, M >= N** (e.g. M=14, N=12 -- employer two months late applying
the raise): no `period_back` fetch happens at all; `total_ratio = real_ratio`
directly, i.e. identical output to today's current code for this specific
case (the real data already covers more than the full cycle, nothing to
project).

**Missing `period_back` data case:** identical output to today's current code
(`total_ratio = real_ratio`) -- the new methodology is additive, it never
makes a previously-working scenario worse, it only improves the
previously-undercounted `0 < M < N` scenario.

## 5. Deflation floor interaction (What I need, item covered in brief's constraints)

**The floor now applies to the final, consolidated `total_ratio`, not to the
raw M-month `real_ratio` alone.** Rationale: `total_ratio` is what actually
drives both `net_pay_clp` and `increase_pct` going forward -- gating the
deflation floor on a sub-component that no longer solely determines the
outcome would be inconsistent. Concretely: `_apply_ipc_step()`'s existing
check `if latest_value < last_increase_index: return ... _NO_INCREASE_PCT`
becomes `if total_ratio < 1: return ... _NO_INCREASE_PCT` -- same semantics
("don't reduce pay"), now evaluated against the number that actually matters.
A tie (`total_ratio == 1`) continues to **not** be treated as deflation,
unchanged from today's `<` (strict) comparison -- same reasoning as
Section 10 of `net-pay-prediction-reimplementation-design-plan.md` (a flat
step still advances the anchor).

## 6. Function signature / call-site changes (What I need, item 5)

New standalone helper, colocated in `payroll_repository_shared.py` next to
`_apply_ipc_step()`:

```python
async def _extrapolate_cycle_ratio(
    *,
    last_increase_period: date,
    last_increase_index: Decimal,
    latest_period: date,
    latest_value: Decimal,
    increase_frequency: int,
    market_data_repository: MarketDataRepository,
) -> Decimal:
    """Return the best-estimate ratio for the employer's full N-month cycle.

    `real_ratio` (latest_value / last_increase_index) only ever reflects the
    M months of IPC actually published since the last real increase -- this
    extrapolates the remaining `increase_frequency - M` months using the
    geometric mean monthly rate observed over the most recent
    `increase_frequency` published months (a trailing window ending at
    `latest_period`, independent of `last_increase_period`), then recompounds
    it on top of `real_ratio` for the full cycle.

    Falls back to `real_ratio` alone -- i.e. today's pre-extrapolation
    behavior -- whenever there is nothing to extrapolate (`months_elapsed`
    already covers or exceeds `increase_frequency`) or the trailing-window
    IPC figure needed to estimate a monthly rate isn't available. Never
    raises on missing *data* (mirrors every other degrade-to-best-available
    guard in _apply_ipc_step()); a pf-rates *outage* still propagates
    PayrollDependencyError from get_economic_index_value(), same as every
    other market-data call in this module.
    """
    real_ratio = latest_value / last_increase_index
    months_elapsed = (
        (latest_period.year * 12 + latest_period.month)
        - (last_increase_period.year * 12 + last_increase_period.month)
    )
    missing_months = increase_frequency - months_elapsed
    if missing_months <= 0:
        return real_ratio
    period_back = add_months(latest_period, -increase_frequency)
    trailing_anchor_value = await market_data_repository.get_economic_index_value(
        _IPC_CODE, period_back.year, period_back.month
    )
    if trailing_anchor_value is None or trailing_anchor_value <= 0:
        return real_ratio
    trailing_ratio = latest_value / trailing_anchor_value
    monthly_ratio = trailing_ratio ** (Decimal(1) / Decimal(increase_frequency))
    return real_ratio * (monthly_ratio ** missing_months)
```

`_apply_ipc_step()` gains one new keyword parameter, `increase_frequency:
int`, and its body changes from computing `latest_value / last_increase_index`
inline to calling the helper above:

```python
async def _apply_ipc_step(
    current_value: Decimal | None,
    *,
    last_increase_period: date | None,
    latest_index: tuple[date, Decimal] | None,
    increase_month: date,
    increase_frequency: int,          # NEW
    market_data_repository: MarketDataRepository,
) -> tuple[Decimal | None, date | None, Decimal]:
    ...
    total_ratio = await _extrapolate_cycle_ratio(
        last_increase_period=last_increase_period,
        last_increase_index=last_increase_index,
        latest_period=latest_period,
        latest_value=latest_value,
        increase_frequency=increase_frequency,
        market_data_repository=market_data_repository,
    )
    if total_ratio < 1:
        return current_value, last_increase_period, _NO_INCREASE_PCT
    increase_pct = quantize_percent((total_ratio - 1) * 100)
    stepped = (
        (current_value * total_ratio).quantize(_NET_PAY_QUANT)
        if current_value is not None
        else None
    )
    return stepped, increase_month, increase_pct
```

`project_future_months()`'s one call site to `_apply_ipc_step()` adds
`increase_frequency=increase_frequency` -- it already receives this value as
its own parameter today, so this is purely plumbing, no new data to source.
`_resolve_extrapolate_cycle_ratio`/`_apply_ipc_step()`'s other callers: there
are none (`_apply_ipc_step` is private to this module, called only from
`project_future_months()`).

## 7. Test plan (What I need, item 6)

New/changed tests, all in `tests/unit/infrastructure/test_payroll_repository.py`
unless noted:

1. **`_extrapolate_cycle_ratio()` unit tests** (new, direct, isolated from
   `_apply_ipc_step()`):
   - Normal case: `0 < M < N`, trailing-window IPC present → geometric
     compounding applied, result matches Section 4's hand-computed
     `1.040462242103401184543536874` (assert via `Decimal` equality, not a
     rounded comparison, to pin the exact arithmetic).
   - `M >= N` guard: assert the result equals `real_ratio` exactly, **and**
     assert the fake `MarketDataRepository`'s `get_economic_index_value` was
     never called for `period_back` (prove the cheap-path claim in Section 2.2
     is real, not just asserted in prose).
   - `M <= 0` degenerate case: same assertions as the `M >= N` guard (folds
     into the same code path, per Section 2.2).
   - Missing trailing-window IPC (`get_economic_index_value` returns `None`
     for `period_back`): result equals `real_ratio` exactly.
   - Trailing-window IPC `<= 0` (defensive, mirrors the existing
     `last_increase_index <= 0` guard): result equals `real_ratio` exactly.
2. **`_apply_ipc_step()` existing tests**: update every call site to pass the
   new `increase_frequency` keyword argument; recompute any test that
   exercises the `0 < M < N` path with a real `period_back` fixture value
   (the existing `112.18`/`113.15` two-point fixtures need a third data point
   added for whichever `period_back` their scenario implies).
3. **Deflation floor interaction** (new): construct a case where `real_ratio`
   alone is mildly inflationary but the trailing-window monthly rate is
   negative enough that `total_ratio < 1` once compounded across
   `missing_months` -- assert the floor fires on the *consolidated* ratio
   (Section 5), i.e. a case that would **not** have triggered the floor under
   today's `real_ratio`-only comparison.
4. **`project_future_months()` / `test_project_future_months_replicates_until_next_increase`**:
   update the existing worked-example assertions (`0.86` → the new
   extrapolated figure, once a real third IPC fixture point is chosen) --
   this test is the brief's own concrete motivating example, it must reflect
   the fixed behavior once this ships.
5. **End-to-end `list_period_ranges()`**: extend
   `test_sqlalchemy_payroll_repository_lists_period_ranges_projects_all_future_months`
   (or add a new dedicated test) with a `FakeMarketDataRepository` seeded with
   three `IPC_CL` points (anchor, trailing-window, latest) reproducing this
   brief's scenario, asserting the resulting `increase` and `net_pay_clp`
   match Section 4's numbers.
6. **100% coverage gate** (`--cov-fail-under=100`, this repo's enforced bar):
   every branch in `_extrapolate_cycle_ratio()` (the `missing_months <= 0`
   guard, the `trailing_anchor_value` None/`<=0` guard, the happy path) needs
   its own dedicated test per item 1 above -- line coverage alone would not
   guarantee the `M >= N` "no fetch happened" claim is actually true, which is
   why that specific assertion (not just "coverage passes") is called out
   explicitly above.

## 8. Action plan

1. **`payroll_repository_shared.py`**: add `_extrapolate_cycle_ratio()`
   (Section 6); update `_apply_ipc_step()`'s signature and body to call it and
   gate the deflation floor on `total_ratio` (Section 5); update its
   docstring to describe the new extrapolation behavior instead of the plain
   `latest_value / last_increase_index` ratio.
2. **`project_future_months()`**: pass `increase_frequency=increase_frequency`
   into its one `_apply_ipc_step()` call site (already has the value, pure
   plumbing).
3. **Tests**: Section 7, in order -- the isolated `_extrapolate_cycle_ratio()`
   unit tests first (fastest feedback on the core math), then the
   `_apply_ipc_step()` call-site updates, then the `project_future_months()`
   worked-example update, then the end-to-end `list_period_ranges()` test.
4. **Docs**: add a new numbered section to
   `net-pay-prediction-reimplementation-design-plan.md` documenting what was
   actually built (same convention this file has followed for every prior
   change to this area, Sections 10-14). `docs/api.md`'s existing wording for
   `increase` ("the same IPC-based ratio used to step net_pay_clp... 0.00 on
   every non-increase month...") remains technically accurate as written (it
   deliberately doesn't describe the internal ratio's exact formula) -- no
   change strictly required there, but a short addition noting the N-month
   extrapolation would improve it; call this out as optional polish, not a
   blocker.
5. **Full quality gate**: `pytest` (100% coverage maintained on every touched
   file), `ruff check`/`ruff format --check`/`mypy`/`vulture` all clean --
   same bar as every other change in this repo. Live-verify against the
   restored local Neon data + local `pf-rates` the same way every prior
   change in this area has been verified (confirm the new extrapolated
   `increase` figure is visible end-to-end on `GET /payroll/period-range`'s
   future entries, not just in unit tests).

No `pf-db` migration, no new port method, no new endpoint, no new external
dependency -- this is a pure computation change inside `_apply_ipc_step()`'s
existing responsibility, reusing data `pf-rates` already publishes and a port
method (`get_economic_index_value`) this codebase already calls twice per
step today.

## 9. Recommendation summary

| Open question | Recommendation |
|---|---|
| N-month window definition | Trailing N calendar months ending at `latest_period` (`period_back = add_months(latest_period, -N)`), independent of `last_increase_period` |
| `N - M <= 0` guard | Skip extrapolation entirely, use `real_ratio` as-is, no new IPC fetch |
| Arithmetic vs. geometric mean | Geometric (`r_m = (1 + r_N)^(1/N) - 1`) -- internally consistent with the rest of this function's multiplicative math, free to implement via `Decimal`'s native fractional `**` |
| New data fetch | Reuse existing `get_economic_index_value(code, year, month)` for `period_back` -- no new port method |
| Missing/invalid trailing-window data | Fall back to `real_ratio` alone -- identical to today's current behavior |
| Deflation floor | Gate on the final consolidated `total_ratio < 1`, not the raw `real_ratio` |

Worked-example bottom line: today's code reports a **0.86%** increase and
steps `net_pay_clp` to `3,145,211.91`; this recommendation reports **4.05%**
and steps to `3,244,420.33` for the exact same underlying scenario -- a
~99,000 CLP difference on one worked example, which is the real magnitude of
the gap the brief asked to close.
