# Investigation: `HEALTH_ADDITIONAL_UF` mismatch on a real import

**Status:** root cause isolated -- **not a code bug**. pf-payroll's calculation
engine and pf-rates' UF value were both proven correct; the gap was narrowed
down to a reference-data question (`contracted_uf` in `health_plans`) that
requires verification against the employee's real Isapre contract, outside
this repo's scope. See the "Closure" section below.
**Opened:** 2026-09-26, right after deploying commits `48a6314` and
`551cf30` (see `git log` in `pf-payroll`). **Root cause isolated the same
day.**

## Context

This session shipped two fixes to `POST /payroll/import/rows`:

1. `48a6314` — `build_imported_contribution_validation()` no longer nulls out
   `expected_health_plan_additional_clp` just because a period has more than
   one `health_plan_id` assigned (that guard predated the `contracted_uf`
   aggregation across every assigned plan done by
   `get_contribution_context()`, and was firing on almost every real
   import).
2. `551cf30` — money fields on `ImportedPeriodRead` /
   `ImportedContributionValidationRead` now render as real JSON numbers
   instead of the quoted string pydantic serializes `Decimal` as by default.

Both fixes are confirmed working in production: numbers come back unquoted,
and the `HEALTH_ADDITIONAL_UF` validation now actually runs instead of
silently returning `null`. Precisely *because* it now runs, it surfaced a
real discrepancy.

## The finding

Re-running the same real payslip (`WALMART-CHILE`, period 2026-08,
`payment_date=2026-08-31`) against `POST /payroll/import/rows`
(`mode=validate`) in production:

```
declared_health_plan_additional_clp:  38013
expected_health_plan_additional_clp:  33516
health_plan_additional_difference_clp: 4497   <- computed is LOWER than declared
warning: "Imported contribution totals do not match the computed payroll
          contributions. HEALTH_ADDITIONAL_UF declared 38013.00 CLP,
          expected 33516 CLP."
```

The other three reconciled concepts in the same response all matched
**exactly** (diff `0` for all three):

- `PENSION_BASE`: declared 367864 == expected 367864
- `PENSION_ADDITIONAL`: declared 42672 == expected 42672
- `HEALTH_BASE`: declared 257505 == expected 257505

That's an important clue: it rules out the contribution rate, the
contribution cap, and the capped taxable base all being wrong -- those are
shared inputs across pension and health. The mismatch is isolated
specifically to the `HEALTH_ADDITIONAL_UF` (UF-denominated Isapre plan
"top-up") calculation.

## Where the calculation lives

`src/payroll/domain/contribution_calculator.py`, `ContributionCalculator.health()`:

```python
contracted_clp = quantize_clp(plan.contracted_uf * plan_uf_value_clp)
additional_amount = max(Decimal("0"), contracted_clp - base_amount)
```

Since `base_amount` was already proven correct (`HEALTH_BASE` matched
exactly), the discrepancy has to live entirely in `contracted_clp`, i.e. in
one of:

- `plan.contracted_uf` -- the value seeded in the `health_plans` table for
  whichever plan(s) got assigned to this period, or
- `plan_uf_value_clp` -- the month-end UF exchange rate resolved for
  `2026-08-31` (see the already-documented pf-rates market-data caching
  caveat on `ImportPayrollRowsRequest`'s docstring).

`plan.contracted_uf` here is the **aggregate** across every `health_plan_id`
snapshot assigned to the period (see `get_contribution_context()` in
`payroll_repository_commands.py`, which sums `contracted_uf` across all
assigned plans as long as they share the same institution -- this is the
exact aggregation behavior that fix `48a6314` started trusting instead of
bailing out on).

## Question raised by the user: why isn't there also a warning on `net_pay`?

Good question, and it has a concrete answer in the code, not an oversight:
`net_pay_difference_clp` and `net_pay_warning` are **not** computed from the
values `ContributionCalculator` just computed (the formula's `33516`). They
are computed like this (`_reconcile_period_net_pay()` in
`payroll_repository_shared.py`):

```python
summary_result = await self._session.execute(
    select(PayrollSummaryModel.net_pay_clp).where(
        PayrollSummaryModel.period_id == period.id
    )
)
expected_net_pay_clp = summary_result.scalar_one_or_none()
...
period.net_pay_difference_clp = (
    period.declared_net_pay_clp - period.expected_net_pay_clp
)
```

`PayrollSummaryModel.net_pay_clp` comes from the `PAY_MV_SUMARY` materialized
view, whose SQL definition (`pf-db/db/01_schema.sql`) is simply:

```sql
SUM(CASE WHEN c.kind = 'income'   THEN i.amount_clp ELSE 0 END) -
SUM(CASE WHEN c.kind = 'discount' THEN i.amount_clp ELSE 0 END) AS net_pay_clp
```

In other words: `expected_net_pay_clp` is the sum of the **already
persisted, declared items** (the `PAY_ITEM` rows that came from the
imported PDF/CSV/JSON, including the declared `38013` for
`HEALTH_ADDITIONAL_UF`), not the sum of the values `ContributionCalculator`
independently recomputes.

In other words, these are **two different checks answering two different
questions**:

1. `contribution_validation` (the one that raised the warning) answers:
   *"does the amount the payslip declared for this concept match what our
   own calculation formula would produce?"*
2. The `net_pay` reconciliation answers: *"do the amounts the payslip
   declared for each item add up exactly to the net pay the same payslip
   declared?"*

Since the real payslip is already internally consistent (its own numbers
already add up correctly, it's a real employer-issued payslip), question (2)
will always come out to a `0` difference regardless of whether any of its
individual concepts match our internal formula or not. That's why it's
entirely possible (and not a bug) to have a warning on
`contribution_validation` without, at the same time, having a difference or
a warning on `net_pay`.

Put another way: if we ever wanted a `HEALTH_ADDITIONAL_UF` mismatch like
this one to also show up on `net_pay`, the `net_pay` reconciliation would
need to be changed to use the amounts *recomputed* by `ContributionCalculator`
instead of the *declared* amounts already persisted as `PAY_ITEM` -- a much
bigger design change than the specific bug we're investigating here, and one
that isn't clearly desirable (it would blur "the payslip is internally
consistent" together with "the payslip matches our formula", which today are
deliberately separate questions).

## Progress (session 2, 2026-09-26): real `/reference-data` data

Production was queried (step 1 of the next steps, read-only):

```
GET /reference-data/health-institutions?include_inactive=true
```
The only active Isapre institution: `ESENCIAL` (`mandatory_rate=0.07`,
matches expectations). The rest (`BANMEDICA`, `COLMENA`, `CONSALUD`,
`CRUZBLANCA`, `FONASA`, `NUEVA_MASVIDA`, `VIDA_TRES`) are inactive.

```
GET /reference-data/health-plans?include_inactive=true
```
There are exactly 3 plans, all three under `ESENCIAL`, all three with
`valid_from=2024-11-01` and `valid_to=null` (i.e. all three are still valid
on `2026-08-31` -- this confirms that `_deduce_health_plan_ids_for_date`
does resolve all 3, not just one, for step 2):

| id | plan_name    | contracted_uf |
|----|--------------|---------------|
| 7  | Adicionales  | 0.79          |
| 8  | Base         | 5.42          |
| 9  | GES          | 0.91          |

Total aggregate (what `get_contribution_context()` sums, same institution):
**7.12 UF**.

### Manual reconstruction (step 4, with exact Decimal math)

Since `base_amount_clp` is already confirmed correct (`HEALTH_BASE` matches
exactly at 257505), and `additional_amount = contracted_clp - base_amount`,
we can solve for what UF value each side *implies*, without yet knowing the
real value pf-rates returned:

```
uf_value implied by the COMPUTED value (33516) = (33516 + 257505) / 7.12
                                                = 40873.7359550561797752808988764...

uf_value implied by the DECLARED value (38013) = (38013 + 257505) / 7.12
                                                = 41505.3370786516853932584269663...

difference = 631.60 CLP per UF  (~1.545% higher in the declared value)
```

This is a fairly clean finding: **if the 7.12 UF aggregate is correct**, the
entire 4497 CLP gap is explained by a single variable -- a UF value ~1.55%
higher used by whoever issued the real payslip than the one our system
resolved for `2026-08-31`. The two implied values (~40874 and ~41505
CLP/UF) are, moreover, perfectly plausible for a mid-2026 UF -- they aren't
outlandish numbers suggesting the 7.12 UF aggregate itself is wrong.

This reorders the hypotheses: the prime suspect is now specifically the
**UF value resolved for `2026-08-31`** (original hypothesis 2), not the
composition of the plan aggregate (original hypothesis 3) -- a consistent
~1.55% gap in a single number (the UF price) is a simpler, cleaner
explanation than guessing which subset of the 3 plans to sum.

## Closure (session 2, continued): the UF value is ruled out as the cause

pf-rates was queried directly:

```
GET /exchange-rates/value?currency_code=UF&rate_date=2026-08-31  (on pf-rates)
-> { "value_clp": 40873.770000 }
```

Plugging this **real, confirmed** value into the domain formula (with the
same rounding `quantize_clp` uses, `ROUND_HALF_UP` to 0 decimals):

```
contracted_clp = 7.12 UF * 40873.77 = 291021.2424
additional_amount = round(291021.2424 - 257505) = 33516
```

**This matches exactly the `expected_health_plan_additional_clp: 33516` the
API returned.** This unambiguously confirms that:

- `resolve_month_end_uf_exchange_rate()` is reading the correct value for
  `2026-08-31`.
- pf-rates has the correct data for that date.
- `ContributionCalculator.health()`'s formula is applying that value
  correctly.

In other words: **there is no code bug in pf-payroll, nor stale data in
pf-rates.** Hypothesis 1 (the UF value) is **completely ruled out**, with
exact, not approximate, evidence.

The user also manually checked whether `41505.34` CLP (the UF implied by
what the payslip *declared*) matches the UF of **any** other nearby date --
**it matches none of them**. This independently rules out the weaker
variant of hypothesis 1 too ("it's using the wrong date, but a real one"):
there is no real UF date that reproduces the declared amount using the
7.12 UF aggregate.

### The real gap, with the UF now confirmed

With the UF fixed at `40873.77` (the correct, confirmed value), the only
thing that can explain the declared `38013` is that the total
`contracted_uf` actually contracted by this employee isn't `7.12` but:

```
contracted_uf implied by the declared value = (38013 + 257505) / 40873.77
                                             = 7.23001572891367740240256771029...

gap vs. our seeded aggregate (7.12)         = 0.11001572891367740240256771029... UF
```

In other words, the real employee appears to be missing **~0.11 UF** of
contracted plan in the `health_plans` table relative to what the real
payslip bills. None of the 3 existing plans (`0.79`, `5.42`, `0.91`) is that
value, nor an obvious combination of them -- it's not that one of the
existing 3 is missing from the sum, but rather that there's possibly a
**fourth component** missing from the reference data, or one of the 3
seeded values is slightly stale.

## Hypotheses (closed)

1. ~~UF value resolved for `2026-08-31` incorrect.~~ **Ruled out**, confirmed
   with real data from pf-rates + the user's manual search across other
   dates.
2. **[only hypothesis still alive] Seeded `contracted_uf` data is incomplete
   or stale for this employee's `ESENCIAL` Isapre plan.** With the UF
   already confirmed, the entire gap (4497 CLP) is explained by a shortfall
   of ~0.11 UF in the total contracted amount seeded in `health_plans`
   (7.12 seeded vs. ~7.23 real, implied). This is **no longer a code
   investigation** -- it's a data/business question: it needs to be
   verified against this employee's real Isapre contract (or against
   whoever issues the payslip) whether a fourth plan component is missing
   from the `health_plans` table, or whether one of the 3 seeded values
   (`Adicionales=0.79`, `Base=5.42`, `GES=0.91`) should be different.
3. ~~Multi-plan auto-deduction picking the wrong set.~~ **Ruled out** as a
   code explanation: it was confirmed that the 3 valid plans are resolved
   and summed correctly; the aggregation itself isn't broken, the seeded
   total simply doesn't match this employee's reality.
4. ~~Rounding/quantization in `quantize_clp`.~~ **Ruled out**: the exact
   reconstruction with the real UF reproduces the API's `33516` down to the
   exact CLP.

## Suggested next steps

1. ~~Query production for every health plan valid on `2026-08-31`.~~
   **Done.**
2. ~~Confirm which `health_plan_ids` `_deduce_health_plan_ids_for_date`
   resolves.~~ **Done.**
3. ~~Confirm the real `uf_value_clp` resolved for `2026-08-31`.~~
   **Done** -- `40873.77`, matches the calculation exactly.
4. ~~Manually reconstruct the calculation.~~ **Done** -- reproduces `33516`
   down to the exact CLP; the gap was isolated 100% to `contracted_uf`.
5. **Pending (outside code scope):** verify against this employee's real
   Isapre contract, or against whoever issues the payslip, whether a
   fourth plan component (`~0.11 UF`) is missing from `health_plans`, or
   whether one of the 3 seeded values is off. If a missing/stale data
   point is confirmed, the fix would be an `INSERT`/`UPDATE` of reference
   data (coordinated per `pf-db`'s rules), **not** a code change in
   `pf-payroll` -- the calculation engine was already proven correct in
   this investigation.

## Related prior context

- `docs/proposals/pdf-import-design-recommendation.md` -- the original PDF
  import design, including the `COMISIÓN AFP` -> `HEALTH_ADDITIONAL_UF`
  mapping bug fixed this same week (see git history: `c152f1a`), which is a
  *different*, already-resolved bug, unrelated to this one.
- `ImportPayrollRowsRequest`'s docstring in
  `src/payroll/interfaces/api/routes/payroll.py` -- documents the pf-rates
  market-data caching side effect mentioned in hypothesis 2.
