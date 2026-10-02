## Context

`GET /payroll/period-range` already projects a salary-base `increase` percentage for
each of the 12 future months in its window (see
`docs/proposals/net-pay-prediction-reimplementation-design-plan.md`, Sections 10-12,
and `docs/proposals/net-pay-prediction-reimplementation-design-recommendation.md`).
The relevant code is `payroll/infrastructure/db/repositories/payroll_repository_shared.py`:

```python
async def _apply_ipc_step(
    current_value: Decimal | None,
    *,
    last_increase_period: date | None,
    latest_index: tuple[date, Decimal] | None,
    increase_month: date,
    market_data_repository: MarketDataRepository,
) -> tuple[Decimal | None, date | None, Decimal]:
    ...
    latest_period, latest_value = latest_index
    if latest_period > increase_month:
        return current_value, last_increase_period, _NO_INCREASE_PCT
    last_increase_index = await market_data_repository.get_economic_index_value(
        _IPC_CODE, last_increase_period.year, last_increase_period.month
    )
    ...
    increase_pct = quantize_percent((latest_value / last_increase_index - 1) * 100)
    stepped = (
        (current_value * latest_value / last_increase_index).quantize(_NET_PAY_QUANT)
        if current_value is not None
        else None
    )
    return stepped, increase_month, increase_pct
```

This only fires at a scheduled increase month (`is_increase_period()`,
`payroll/shared/dates.py`), which is driven by two `PAY_EMPLOYER` columns
(`pf-db/db/01_schema.sql` lines 208-213, both nullable, both `SMALLINT`):

```sql
first_increase_period_year               SMALLINT
CHECK (first_increase_period_year BETWEEN 1990 AND 2100),
first_increase_period_month              SMALLINT
CHECK (first_increase_period_month BETWEEN 1 AND 12),
increase_frequency                       SMALLINT
CHECK (increase_frequency > 0),
```

`last_increase_index` is the IPC published **for the month of the last real
increase** (`last_increase_period`, resolved by `resolve_last_increase_period()` in
`payroll/shared/dates.py`). `latest_value`/`latest_period` is simply **whatever IPC
figure `pf-rates` has most recently published** (`get_latest_economic_index()`),
regardless of how many months separate the two. The percentage applied to the whole
cycle is the raw ratio between those two single data points — there is no concept
of "months elapsed" or "months configured" anywhere in this function today.

**This is exactly the gap the attached instruction describes, and we already have a
real worked example of it in the test suite** —
`test_project_future_months_replicates_until_next_increase`
(`tests/unit/infrastructure/test_payroll_repository.py`): last real increase
2026-04 (IPC 112.18), latest published IPC 2026-08 (113.15), employer's configured
`increase_frequency` is 12 months. Only **M = 4** months of real IPC movement are
being used to price a cycle configured for **N = 12** months, and the resulting
`increase_pct` (0.86%) is reported and applied as if it already represented the
full annual adjustment — it does not, it represents a third of a year's worth of
observed inflation, with no correction for the other 8 months.

## The instruction (translated/adapted into this codebase's terms)

I want to adjust the salary-increase projection logic for future periods. Right
now, `_apply_ipc_step()` prices a scheduled increase using only the IPC variation
between `last_increase_period` (the anchor) and whatever IPC figure
`pf-rates`/`get_latest_economic_index()` happens to have published most recently —
**M months** of real data, in the terms above. But the employer's actual cadence,
`PAY_EMPLOYER.increase_frequency` (**N months**), is wider: in the worked example
above, M=4 but N=12. The projection is missing the other N-M months entirely
instead of estimating them, which understates the true expected adjustment.

**Proposed methodology:**

1. **Estimated average monthly rate.** Take the cumulative IPC variation over the
   last N *published* months (N = `increase_frequency`) and divide it by N.
2. **Observed segment.** Compute the real cumulative IPC variation over the M
   months of actual published data elapsed since `last_increase_period` (M=4 in
   the worked example).
3. **Project the missing segment and consolidate.** Project the remaining N-M
   months by multiplying the step-1 average monthly rate by (N-M) (8 months in the
   worked example), and add that to the real cumulative variation from step 2.

## Blind spots flagged in the original instruction (confirm/resolve each one against this codebase)

- **Edge case `N - M <= 0`.** If the configured cadence has already elapsed or been
  exceeded (`M >= N` — e.g. the employer is contractually late applying the raise),
  `(N - M)` is zero or negative. The logic needs an explicit guard against a
  negative/duplicated projection. `_apply_ipc_step()` has no concept of M at all
  today, so this guard does not exist in any form yet — it must be designed from
  scratch, not merely ported.
- **`PAY_EMPLOYER` is `NULL`.** The instruction assumes this still needs a default
  rule. **It already has one, confirm this in the recommendation rather than
  re-solving it:** `SqlAlchemyPayrollQueryRepository._resolve_increase_frequency()`
  defaults a `NULL` `increase_frequency` to 12, and `_resolve_first_increase_period()`
  defaults a `NULL` `(first_increase_period_year, first_increase_period_month)` pair
  to `employer_started_at` plus that same resolved frequency
  (`payroll_repository_queries.py`). Whatever N/M logic this brief leads to must
  compose with those two existing defaults unchanged, not introduce a second,
  competing default.
- **Simple vs. compound IPC math.** Linear sum/division ignores IPC's compounding
  effect (`IPC_acumulado = ∏(1 + r_i) - 1`). If financial rigor is required, the
  equivalent geometric monthly rate is `r_m = (1 + r_N)^(1/N) - 1`, and the
  consolidated projection should be `(1 + r_real) · (1 + r_m)^(N-M) - 1` rather than
  a plain sum. Note that today's *existing* code already behaves "geometrically" in
  the narrow sense that it computes a single ratio
  (`latest_value / last_increase_index`) rather than summing monthly deltas — but it
  has never had to average or re-compound a rate across a sub-window the way this
  new step-1/step-3 split requires, so the arithmetic-vs-geometric choice here is a
  genuinely new decision, not a continuation of an existing convention.

## Additional constraints specific to this codebase (not in the original instruction)

- **A new IPC data point is required that nothing in this codebase currently
  fetches.** Step 1 needs IPC N months before the latest published point (e.g.
  2025-08 in the worked example, to pair with 2026-08 for a trailing 12-month
  window) — a `get_economic_index_value(code, year, month)` call for a month that
  is neither `last_increase_period` nor "the latest published" (the only two
  lookups `_apply_ipc_step()` performs today). Confirm: is `get_economic_index_value`
  (already a `MarketDataRepository` port method, already used elsewhere) sufficient
  as-is for this new lookup, or does the "last N published months" framing need a
  new port method (e.g. resolving the month that is N steps before the latest
  published one, independent of calendar months-ago arithmetic, in case of gaps in
  publication)? State and justify.
- **New missing-data path to design.** This adds a *third* IPC fetch to a function
  that currently only ever has two ways to degrade (no anchor to compare against; no
  IPC data at all) plus the already-added deflation floor (Section 10 of the
  net-pay-prediction plan). What happens when the new N-months-back IPC figure does
  not exist (e.g. the index has not been published for that far back, or `pf-rates`
  has a gap)? Candidates to evaluate: fall back to today's current (M-only) behavior,
  degrade `increase_pct` to `0.00` same as every other missing-data path in this
  function, or something else — pick one and justify it the same way the existing
  degradation paths are each justified in `_apply_ipc_step()`'s own docstring.
- **`_apply_ipc_step()` is called once per scheduled increase month encountered
  while walking all 12 future months** (`project_future_months()`), potentially
  more than once in the same request if `increase_frequency` is short enough. M
  (months elapsed since that specific `last_increase_period`) must be recomputed
  per call, not assumed constant across the whole request.
- **This feeds both `net_pay_clp` *and* `increase_pct`.** `_apply_ipc_step()`
  returns one ratio used for both the displayed percentage and to scale
  `current_value` into the next stepped `net_pay_clp`
  (`net-pay-prediction-reimplementation-design-plan.md` Section 12 explicitly chose
  to derive `increase_pct` from the *raw* ratio rather than re-deriving `net_pay_clp`
  from an already-quantized percentage, specifically to avoid compounding rounding
  error). Whatever new consolidated rate this brief produces must replace that same
  single ratio for *both* consumers consistently — do not special-case one over the
  other.
- **Must not regress the deflation floor** (`net-pay-prediction-reimplementation-design-plan.md`
  Section 10): a negative step is still held flat instead of reducing pay. State
  explicitly whether that floor now applies to the *consolidated* projected rate (my
  expectation) or still only to the simple M-month-only ratio.
- **Existing hexagonal/testing conventions apply unchanged:** `Decimal` only (never
  `float`) for every rate/percentage computed along the way; `_apply_ipc_step()` and
  `project_future_months()` currently propagate `PayrollDependencyError` and let
  `list_period_ranges()` be the single try/except boundary — any new pf-rates call
  this methodology needs must follow that same convention, not introduce a second
  catch point. 100% branch-relevant test coverage is the enforced bar in this repo
  (`--cov-fail-under=100`) — the `M >= N` guard, the new missing-N-months-back-data
  path, and the arithmetic-vs-geometric choice each need their own dedicated test,
  the same way the existing deflation-floor guard and zero-salary-predecessor guard
  each got one (see `net-pay-prediction-reimplementation-design-plan.md` Sections 10
  and 12 for precedent).

## What I need

1. **Pin down the exact N-month window.** "The last N published months" needs an
   unambiguous definition in terms of real `(year, month)` pairs: is it the N
   months ending at the latest published period (N data points spanning N-1 month
   gaps), or N month-gaps (N+1 data points, i.e. latest published vs. N months
   before it)? Work the brief's own example through your chosen definition
   explicitly.
2. **Resolve the `N - M <= 0` guard.** State the exact behavior when the cadence has
   already elapsed or been exceeded — most likely: skip the projection step
   entirely and use the real M-month cumulative variation as-is (it already covers,
   or exceeds, the configured cycle), but confirm and justify.
3. **Arithmetic vs. geometric: pick one, justify it, and show both worked
   examples.** Compute the brief's worked example (M=4, N=12, anchor IPC 112.18,
   latest published IPC 113.15) both ways so the actual numeric difference between
   the two approaches is visible, not just the formulas.
4. **New data-fetch plan.** Confirm whether the existing
   `MarketDataRepository.get_economic_index_value()` port method is sufficient for
   the new N-months-back lookup, or propose a port change -- and define the
   degradation behavior when that new lookup comes back empty.
5. **Function signature / call-site changes.** Show the updated
   `_apply_ipc_step()` signature (and any new helper functions it needs), and how
   `project_future_months()`'s call site changes to supply `increase_frequency`
   (already available there) through to it.
6. **Test plan.** Enumerate the new/changed test cases this needs, including the
   `N - M <= 0` guard, the new missing-data path, and an end-to-end
   `list_period_ranges()` assertion using the brief's own worked example recomputed
   under your chosen methodology.
7. **Action plan.** Smallest independently-shippable slice first, same expectation
   as every other proposal in this folder.

## Response format

- **Deliverable:** a new Markdown file (not an inline reply, not a code PR), placed
  in the same folder as this document (`docs/proposals/`), named so it is clearly
  the recommendation that answers this brief (e.g.
  `future-increase-ipc-extrapolation-design-recommendation.md`).
- Ground every claim in the actual code (`payroll_repository_shared.py`,
  `shared/dates.py`, `payroll_repository_queries.py`,
  `application/ports/repositories.py`, `pf-db/db/01_schema.sql`) the way prior
  recommendations in this folder have — cite file/line-level evidence, don't assume.
- Work the brief's own 2026-04/2026-08, N=12 example through fully under your
  recommended methodology, end to end (IPC data points used, the resulting
  `increase_pct`, and the resulting stepped `net_pay_clp`).
- End with your recommendation (window definition + guard + arithmetic-vs-geometric
  + new-data-fetch design) and why.

## Stack

- Language: Python (FastAPI, hexagonal, already established in pf-payroll).
- Touches: `payroll/infrastructure/db/repositories/payroll_repository_shared.py`
  (`_apply_ipc_step`, `project_future_months`), `payroll/shared/dates.py`
  (`is_increase_period`, `resolve_last_increase_period`), possibly
  `payroll/application/ports/repositories.py` (`MarketDataRepository`) if a new port
  method is justified. No `pf-db` schema change expected (`PAY_EMPLOYER`'s
  `increase_frequency`/`first_increase_period_*` columns already exist and already
  have resolved defaults) — flag explicitly if your recommendation disagrees.
- No new infrastructure: this is pure computation against data `pf-rates` already
  publishes (`IPC_CL` via `get_economic_index_value`/`get_latest_economic_index`),
  not a new external dependency.
