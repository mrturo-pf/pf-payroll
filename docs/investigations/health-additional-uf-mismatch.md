# Investigation: `HEALTH_ADDITIONAL_UF` mismatch on a real import

**Status:** **Resolved and closed**, with one deliberately deferred
follow-up. The original single-period discrepancy (2026-08) was fixed in
production. Session 3 found the fix's date boundary was 2 months off and
found 3 more missing plan tiers. Session 4 obtained an independent
government record (official Cartola de Cotizaciones) that (a) proved the
CSV's declared amounts are 100% correct with zero mismatches, and (b)
validated the 3 missing tiers locally. The user applied both fixes to
Neon. Session 5 confirmed production behaves identically to the local
validation (20 of 22 periods reconcile exactly) and closed the last
residual (`2025-02`, `$2,860`) as non-actionable under the (then-binary,
non-prorated) plan-validity model. Session 9 implemented day-level
proration in the domain calculation itself, removing that specific
limitation -- but `2025-02` still needs a deliberate reference-data
decision (an actual `valid_from` inside February) before it will reconcile;
see Session 9 below. Nothing else left open that warrants further code or
reference-data changes.
**Opened:** 2026-09-26, right after deploying commits `48a6314` and
`551cf30` (see `git log` in `pf-payroll`). Root cause for the single 2026-08
period isolated and resolved the same day (see "Resolution" below).
Reopened same day for the full 22-period pattern ("Session 3"), then
resolved and validated the same day ("Session 4").

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

## Resolution (2026-09-26, same day)

Two independent fixes closed this out completely:

1. **Reference data corrected.** The `Base` health plan row for `ESENCIAL`
   was updated from `5.42 UF` (valid through `2026-07-31`) to `5.53 UF`
   (valid from `2026-08-01`), matching the employee's real Isapre contract.
   New aggregate: `0.79 + 5.53 + 0.91 = 7.23 UF` -- exactly the value this
   investigation had already reverse-engineered as implied by the declared
   amount (see "The real gap, with the UF now confirmed" above).
2. **Reconciliation tolerance added.** A `100 CLP` tolerance
   (`_RECONCILIATION_TOLERANCE_CLP`) was added to contribution reconciliation
   (`PENSION_BASE`, `PENSION_ADDITIONAL`, `HEALTH_BASE`, and
   `HEALTH_ADDITIONAL_UF`) to absorb rounding noise between the declared
   payslip amount and the independently recomputed one -- matching an
   existing `100 CLP` convention already used elsewhere
   (`ComplementaryInsuranceValidationService`). The exact
   `*_difference_clp` fields still expose the raw delta; only the warning is
   suppressed when within tolerance.

**Confirmed against production** by re-running the exact same real payslip
(`WALMART-CHILE`, period 2026-08) against `POST /payroll/import/rows`
(`mode=validate`):

```
declared_health_plan_additional_clp:  38013
expected_health_plan_additional_clp:  38012   (7.23 UF * 40873.77 - 257505, rounded)
health_plan_additional_difference_clp: 1
warning: null   <- within the 100 CLP tolerance, no warning raised
```

All four reconciled contribution concepts (`PENSION_BASE`, `PENSION_ADDITIONAL`,
`HEALTH_BASE`, `HEALTH_ADDITIONAL_UF`) and `net_pay` now match with zero or
tolerance-absorbed difference, and `complementary_insurance_validation`
returns no warnings either. Nothing left open on this investigation.

## Related prior context

- `docs/proposals/pdf-import-design-recommendation.md` -- the original PDF
  import design, including the `COMISIÓN AFP` -> `HEALTH_ADDITIONAL_UF`
  mapping bug fixed this same week (see git history: `c152f1a`), which is a
  *different*, already-resolved bug, unrelated to this one.
- `ImportPayrollRowsRequest`'s docstring in
  `src/payroll/interfaces/api/routes/payroll.py` -- documents the pf-rates
  market-data caching side effect mentioned in hypothesis 2.

## Session 3 (2026-09-26, same day): the fix's own boundary was wrong, and a wider gap surfaced

The "Resolution" above closed the single 2026-08 period that had been
manually reported. This session ran the **full 22-period real CSV**
(`WALMART-CHILE`, 2024-11 through 2026-08, via the CLI's `import-payroll`,
which exercises the exact same pipeline as `POST /payroll/import`) through
the same reconciliation, now with a real Neon-restored local database
(real `health_plans`, real `RAT_EXCH_RATE`, real contribution caps -- no
synthetic seed data). 20 of the 22 periods conflicted.

### Method: reverse-engineer `contracted_uf` per period from the real, declared numbers

For each period, using the exact `payment_date` from the CSV, the real UF
value for that date (`RAT_EXCH_RATE`), and the declared `health_base` (which,
as already established in "Session 2", matches `HEALTH_BASE` exactly and can
stand in for `base_amount_clp`):

```
implied_contracted_uf = (declared_health_plan_additional + declared_health_base) / uf_value_for(payment_date)
```

Result (full table, `payment_date` order):

| payment_date | implied contracted_uf |
|---|---|
| 2024-11-28 | 2.9704 (partial month, 13 worked days -- not a clean signal) |
| 2024-12-27 | 5.9025 |
| 2025-01-30 | 6.1456 |
| 2025-02-27 | 6.2224 |
| 2025-03-28 .. 2025-07-30 | **~6.64** (tight plateau: 6.6426 / 6.6411 / 6.6409 / 6.6413 / 6.6391) |
| 2025-08-28 .. 2025-12-27 | **~6.89** (tight plateau: 6.8960 / 6.8900 / 6.8918 / 6.8900 / 6.8927) |
| 2026-01-29 .. 2026-02-26 | **~7.00** (tight plateau: 6.9991 / 7.0020) |
| 2026-03-28 .. 2026-05-28 | **~7.12** (tight plateau: 7.1200 / 7.1224 / 7.1289) |
| 2026-06-26 .. 2026-08-28 | **~7.23** (tight plateau: 7.2319 / 7.2300 / 7.2307) |

This is not noise -- these are sharp, multi-month plateaus with sub-0.01 UF
variance inside each one. The employee's real contracted health plan total
clearly stepped up several distinct times across the 22-month window.

### Finding 1 (confirmed and fixed): the existing 7.12 -> 7.23 boundary was 2 months late

Only the last two plateaus (`~7.12` and `~7.23`) had any corresponding row in
`health_plans` at all -- from the "Resolution" above: `Base` at `5.42 UF`
(`+0.79 Adicionales +0.91 GES = 7.12`) and `Base` at `5.53 UF` (`= 7.23`).
But their `valid_from`/`valid_to` boundary was set at **2026-08-01**, while
the reverse-engineered data shows the real cutover is between the
2026-05-28 and 2026-06-26 payments -- **two months earlier**. The old row's
`valid_from=2024-11-01` was also wrong in the same direction: it silently
covered periods (2024-11 through 2026-02) that the data shows had a much
lower real contracted amount.

**Fix applied to production (Neon), by the user directly:**

- `Base` (`5.42 UF`, aggregate `7.12`): `valid_to` moved from `2026-07-31` to
  **`2026-05-31`**.
- `Base` (`5.53 UF`, aggregate `7.23`): `valid_from` moved from `2026-08-01`
  to **`2026-06-01`**.

**Verified locally first** (same boundary values applied to the Neon-restored
local database, then the full CSV re-imported): periods `2026-03`, `2026-04`,
`2026-06`, `2026-07`, and `2026-08` all dropped out of the conflict list
entirely -- i.e. `HEALTH_ADDITIONAL_UF` now reconciles exactly (within the
100 CLP tolerance) for all five. Conflicting periods went from 20 to 17.

### Finding 2 (newly opened, NOT fixed): three more plan tiers are missing from `health_plans` entirely

The remaining 17 conflicting periods (2024-11 through 2026-02) are not
explained by any boundary-date error -- there is simply no row in
`health_plans` for their implied `contracted_uf` values at all. Subtracting
the two constant components (`Adicionales=0.79`, `GES=0.91`) from each
plateau's total gives the missing `Base` value each period would need:

| Plateau (payment dates) | Implied total UF | Implied missing `Base` UF |
|---|---|---|
| 2025-03-28 .. 2025-07-30 | ~6.64 | **~4.94** |
| 2025-08-28 .. 2025-12-27 | ~6.89 | **~5.19** |
| 2026-01-29 .. 2026-02-26 | ~7.00 | **~5.30** |
| 2024-11-28 .. 2025-02-27 | 2.97 -> 6.22 (ramping, not a plateau) | not analyzed -- likely a prorated/partial first contract, needs the real source rather than back-calculation |

Unlike Finding 1 (a pure date-boundary bug, fixable from data already in
hand), these three values do not exist anywhere in the current reference
data under any date range -- they would have to be **inserted** as new
`health_plans` rows, and per this investigation's own earlier conclusion
("Hypotheses (closed)", #2), that requires confirmation from the real
Isapre contract or the payslip issuer, not a back-calculation from the
declared payslip numbers alone (the whole point of the reconciliation check
is that the declared number is exactly what's in question -- using it to
derive its own reference data would make the check circular for this
employee, even though the arithmetic reconstruction above is a solid lead).

**Not fixed yet.** Needs confirmation of the real contracted `Base` UF value
for each of the three plateaus (and the 2024-11..2025-02 ramp) before any
`health_plans` INSERT/UPDATE is made.

### Finding 3 (hypothesis, unconfirmed): the earliest 3 periods may not be a tier problem at all

The 2024-11..2025-01 gap is qualitatively different from the three clean
plateaus above -- it is **not** a tight plateau (implied UF ramps
2.97 -> 5.90 -> 6.15, never settling), and the absolute CLP gap is far
larger (30k-160k CLP) than any of the later, cleaner mismatches
(4.5k-19k CLP). Two facts point away from "wrong tier" and toward
"no `ESENCIAL` Isapre plan should have applied at all yet":

- `PAY_EMPLOYER.started_at = 2024-11-18` for this employee -- November 2024
  is their actual hire month (13 worked days, matching a Nov 18-30 partial
  month).
- `declared_health_plan_additional_clp = 0.00` for **both** 2024-11 (partial
  month, could be prorating) **and** 2024-12 (a full 30-worked-day month,
  ruling out proration as the explanation -- confirmed separately that
  `worked_days` is never even read by `ContributionCalculator` or
  `get_contribution_context()`, so there is no proration logic to blame in
  the first place).

The likely real-world explanation: a newly hired employee in Chile
typically starts on `FONASA` (the public, no-additional-cost health system)
and only switches to an Isapre plan like `ESENCIAL` after some onboarding
period -- during which `HEALTH_ADDITIONAL_UF` is correctly `0` because there
is no Isapre "top-up" to compute (see `ContributionCalculator.health()`:
`additional_amount_clp` is forced to `0` whenever the institution kind is
not `ISAPRE`, regardless of `contracted_uf`). If that's what happened here,
the bug isn't a wrong `contracted_uf` value at all for these 3 periods --
it's that `_deduce_health_plan_ids_for_date()` (in
`payroll_repository_imports.py`) has no way to know this specific employee
wasn't yet enrolled in `ESENCIAL`, because `health_plans` is purely
institution-level reference data (shared across every employee at that
employer), not a per-employee enrollment history -- it will always resolve
*some* `ESENCIAL` plan for any date `>= 2024-11-01`, whether or not this
particular employee was actually enrolled there yet.

**Not confirmed.** This is a hypothesis consistent with the shape of the
data, not a proven fact -- needs confirming whether this employee was
really on `FONASA` (or unenrolled) for their first ~3 months, and if so,
the exact month they switched to `ESENCIAL`. If confirmed, the fix is still
a reference-data question, not a code bug, but it would need a design
decision (currently out of scope): either a per-employee health-institution
enrollment record, or simply excluding those periods from automatic plan
deduction so they import without a `HEALTH_ADDITIONAL_UF` conflict.

### What would unblock Findings 2 and 3

Repo-internal sources were checked and ruled out as already containing this
data:

- `pf-db/db/04_seed_real.sql` (the local synthetic seed) only ever encoded a
  single flat `Base = 5.42 UF` valid from `2024-11-01` forever -- it
  predates even the split this investigation's "Resolution" section fixed,
  so it cannot be used to recover the missing tiers either.
- `pf-payroll/secrets/` only has one real payslip PDF
  (`Liquidación_202608.PDF`, August 2026) -- none for the earlier plateaus,
  so there's no line-item-level real payslip to cross-check against for
  those months.

What would actually close this out: either (a) more real payslip PDFs for
representative months in each plateau (e.g. one from 2025-03..07, one from
2025-08..12, one from 2026-01..02, and ideally one from 2024-11..2025-02
to confirm/deny the FONASA hypothesis), which the existing PDF-import
template (`walmart-chile-v1`) could parse directly, or (b) the real
enrollment/contract history from HR or the Isapre directly.

### Status after Session 3

- Finding 1: **confirmed and applied to Neon** (by the user, directly).
- Finding 2: **open**, blocked on real-world confirmation of 3 missing
  plan tiers for 2025-03..2026-02.
- Finding 3: **open, unconfirmed hypothesis**, blocked on confirming whether
  this employee was on `FONASA` (not `ESENCIAL`) for 2024-11..2025-01.

## Session 4 (2026-09-26, same day): an external record confirms everything, and closes this out

The user supplied an official **Cartola de Cotizaciones** (a Chilean
pension/health contribution statement, independent of this codebase or the
employer's own payroll system) covering this exact employee
(`Arturo Andres Mendoza Benavides`, RUT `25.556.430-7`) from October 2024
through August 2026.

### Step 1: the CSV's declared amounts are proven 100% correct

For every period `Pagador = CORPORATIVE CHILE S.A`, the cartola's `Monto pagado`
column was compared against `declared_health_base_clp + declared_health_plan_additional_clp`
from the CSV. **All 22 periods matched exactly, zero mismatches.** This is
an external, independent confirmation -- not a self-consistency check -- so
any remaining gap is now known with certainty to live entirely in
*our reference data*, not in a bad CSV extraction.

### Step 2: the November 2024 anomaly explained (unrelated to health plan tiers)

The cartola shows **two separate payers for November 2024**:
`Servicios Clinica Alemana Limitada` (RUT `77.413.290-2`, already present in
our `PAY_EMPLOYER` table as a *different* employer) paid `$150.853`, and
`CORPORATIVE CHILE S.A` paid `$113.535` -- the latter matching the CSV exactly.
October 2024 shows *only* Clinica Alemana (`$236.182`, full month). This
confirms the employee transferred from Clinica Alemana to Walmart-Chile
mid-November 2024, consistent with `PAY_EMPLOYER.started_at = 2024-11-18`
for Walmart-Chile. The cartola also shows two **self-payments** by the
employee directly (not through either employer's payroll): `$12.255` for
Diciembre 2024 and `$2.840` for Enero 2025 -- both paid on the same date,
`31/03/2025`, suggesting a retroactive top-up once his Isapre paperwork with
the new employer was fully processed.

### Step 3: Finding 3 (the "was he on FONASA" hypothesis) is ruled out -- a simpler explanation was already sufficient

With the reference-data fix from Step 4 below in place (the 3 new `Base`
tiers, correctly dated so they only apply from 2025-03-01 onward), 2024-11,
2024-12, *and* 2025-01 all reconcile to `expected = 0`, matching their
declared `0` exactly -- **without needing any special-case employment-gap
logic.** The reason: for those 3 months, only `Adicionales` (0.79 UF) and
`GES` (0.91 UF) are valid (no `Base` plan applies pre-2025-03), so
`contracted_clp` (`1.70 UF * uf_value`, roughly 60k-65k CLP) is *smaller*
than `base_amount_clp` (declared `HEALTH_BASE`, already 113k-236k CLP for
these periods, since it scales with salary/cap) -- and
`additional_amount = max(0, contracted_clp - base_amount_clp)` is
therefore `0` by the formula alone. **Finding 3 is ruled out**: there was no
need to model a FONASA/enrollment-gap period at all; the wrongly-early
`valid_from=2024-11-01` on the old `Base=5.42` row (fixed in Step 4) was
the entire explanation for these 3 periods too, exactly like the other
plateaus.

### Step 4: the 3 missing tiers from Finding 2, applied and validated locally

With the CSV numbers now externally verified (Step 1) and the existing
`Base=5.42` row's `valid_from` understood to be wrong for its entire early
range (Step 3), the reverse-engineered tiers from "Session 3" were applied
to the **local** database (not yet Neon) to validate them empirically
before recommending them for production:

```sql
-- Move the existing 5.42 UF tier's start forward (it doesn't really start
-- until March 2026, not November 2024 -- see the plateau table above).
UPDATE "PAY_HLTH_PLAN" SET valid_from = '2026-03-01' WHERE id = 8;

-- Insert the 3 previously-missing Base tiers.
INSERT INTO "PAY_HLTH_PLAN" (institution_id, valid_from, valid_to, plan_name, contracted_uf)
  SELECT institution_id, '2025-03-01', '2025-07-31', 'Base', 4.94 FROM "PAY_HLTH_PLAN" WHERE id=8;
INSERT INTO "PAY_HLTH_PLAN" (institution_id, valid_from, valid_to, plan_name, contracted_uf)
  SELECT institution_id, '2025-08-01', '2025-12-31', 'Base', 5.19 FROM "PAY_HLTH_PLAN" WHERE id=8;
INSERT INTO "PAY_HLTH_PLAN" (institution_id, valid_from, valid_to, plan_name, contracted_uf)
  SELECT institution_id, '2026-01-01', '2026-02-28', 'Base', 5.30 FROM "PAY_HLTH_PLAN" WHERE id=8;
```

Resulting `health_plans` timeline (local DB, all non-overlapping):

| id | plan_name | valid_from | valid_to | contracted_uf |
|---|---|---|---|---|
| 9 | GES | 2024-11-01 | (open) | 0.91 |
| 7 | Adicionales | 2024-11-01 | (open) | 0.79 |
| 11 | Base | 2025-03-01 | 2025-07-31 | 4.94 |
| 12 | Base | 2025-08-01 | 2025-12-31 | 5.19 |
| 13 | Base | 2026-01-01 | 2026-02-28 | 5.30 |
| 8 | Base | 2026-03-01 | 2026-05-31 | 5.42 |
| 10 | Base | 2026-06-01 | (open) | 5.53 |

Re-running the full 22-period CSV import against this corrected local
database: **conflicting periods dropped from 20 to 2.** Every one of
2024-11, 2024-12, 2025-01, 2025-03..2025-07, 2025-08..2025-12, and
2026-01..2026-02 now reconciles exactly (within the 100 CLP tolerance).

### Remaining open items (both minor)

1. **2025-02**: declared `$2,860` vs. expected `$0`, a `$2,860` residual --
   the only period still outside tolerance. Very plausibly a partial-month
   proration as the new `Base` plan enrollment took effect mid-February
   (the clean `4.94 UF` plateau starts cleanly the very next period,
   2025-03), and lines up in magnitude with the `$2,840` self-payment the
   cartola shows for "Enero 2025" (paid `31/03/2025`, so plausibly a
   slightly-delayed record). Small dollar amount, not blocking; left open
   rather than guessed at.
2. **2026-05**: pre-existing, already-known `1 CLP` net_pay rounding note,
   unrelated to health plans (see "The finding" section from Session 1) --
   absorbed by the reconciliation tolerance, not a real conflict.

### Status after Session 4

- Finding 1 (boundary 2 months late): **confirmed, applied to Neon**
  (by the user, in Session 3).
- Finding 2 (3 missing tiers): **confirmed and validated locally** with the
  exact values/dates above (4.94 UF / 5.19 UF / 5.30 UF, non-overlapping
  date ranges as tabulated). **Not yet applied to Neon** -- pending the
  user's decision on whether to apply as-is (validated against an
  independent government record, high confidence) or seek one more level of
  confirmation (e.g. the exact plan-name breakdown, which the cartola
  doesn't provide -- it only confirms the *total*, and our formula only
  needs the total anyway since it sums every assigned plan's `contracted_uf`
  regardless of name).
- Finding 3 (FONASA/enrollment-gap hypothesis): **ruled out** -- not needed;
  the corrected `Base` plan date ranges alone explain 2024-11..2025-01 too.
- Only 2 periods remain outside tolerance across the whole 22-period real
  import, both minor and explained above.

## Session 5 (2026-09-26, same day): Finding 2 applied to Neon; the last residual investigated and closed as a known limitation

The user applied Session 4's SQL directly to Neon (the 3 new `Base` tiers +
the `valid_from` correction on the existing `5.42` row). Re-running the
full 22-period CSV import against the local database (already carrying the
same values from Session 4's local validation) reproduces exactly the same
2 remaining periods predicted -- confirming the production fix behaves
identically to the local validation, with no surprises:

- `2026-05`: the pre-existing, already-documented `1 CLP` net_pay rounding
  note (see Session 1's "The finding") -- unrelated to health plans,
  absorbed by tolerance for every *other* concept.
- `2025-02`: `declared_health_plan_additional_clp=2860` vs.
  `expected=0`, a `$2,860` residual -- the last open item.

### Digging into the `2025-02` residual

The full `contribution_validation` for this period confirms the mismatch is
**cleanly isolated**: `PENSION_BASE`, `PENSION_ADDITIONAL`, and
`HEALTH_BASE` all match their declared values exactly (`difference_clp:
0.00`) -- only `HEALTH_ADDITIONAL_UF` is off, ruling out any broader
data-entry problem for this period.

**Proportion analysis:** `2025-02`'s declared `$2,860` is `14.9%` of
`2025-03`'s (the first clean month of the newly-confirmed `4.94 UF` tier)
`$19,214`. February 2025 has 28 days; `14.9%` of 28 is `~4.2` days --
implying the real plan enrollment became effective around **February 24,
2025**, with the payslip showing a manually prorated partial-month charge.
This is directionally consistent with the Cartola-documented top-up
activity right around this same boundary (the `$2,840` self-payment posted
against "Enero, 2025", paid `31/03/2025` -- close in both timing and
amount, though not identical, suggesting the same underlying
enrollment-processing event rather than two unrelated coincidences).

**Why this can't be modeled with a reference-data fix alone:** confirmed by
re-reading `get_contribution_context()` and `ContributionCalculator.health()`
that plan validity is a **binary** check against a single `reference_date`
(the period's `payment_date`) -- there is no day-level proration anywhere
in the pipeline. Inserting a `health_plans` row with `valid_from` set to
any day in February would make `2025-02` resolve to either `$0` (if
`valid_from` lands after `2025-02-27`) or the **full** `~$19,214` (if on or
before) -- never the actual prorated `$2,860`. Reproducing this specific
number would require adding day-based proration to the domain calculation
itself, a materially bigger change than a reference-data correction, for a
single non-recurring historical `$2,860` CLP discrepancy.

### Status after Session 5 -- investigation closed

- Finding 1: **confirmed, applied to Neon.**
- Finding 2: **confirmed, applied to Neon**, reconciles as validated.
- Finding 3: **ruled out**, no action needed.
- `2026-05` (1 CLP net_pay): pre-existing, unrelated, absorbed by tolerance
  aside from the raw `1 CLP` diff value -- no action.
- `2025-02` (`$2,860` health additional): **accepted as a known, understood,
  non-actionable limitation.** Root cause (a real prorated partial-month
  enrollment charge) is understood with reasonable confidence, but cannot
  be reproduced by the current binary plan-validity model without adding
  day-level proration -- a disproportionate change for one historical
  `$2,860` CLP period. No further action planned unless day-level
  proration becomes valuable for other reasons.

**This investigation is closed.** Of the original 22-period real import, 20
reconcile exactly and 2 have small, understood, documented residuals
neither of which represents a code defect in pf-payroll or pf-rates.

## Session 6 (2026-09-26, same day): real payslip PDFs confirm/close both remaining residuals

The user supplied two real payslip PDFs to cross-check the two remaining
residuals. One turned out to be for the wrong period; the other closed the
`2026-05` `1 CLP` mystery with certainty.

### `202602.pdf` is Febrero **2026**, not Febrero 2025 -- doesn't apply to the open `2025-02` residual

The PDF's own header states `MES: Febrero, AÑO: 2026` (confirmed by
cross-checking every line item -- `ESENCIAL LEGAL 250.681`,
`COMISIÓN AFP P. VITAL 41.541`, `FONDO RETIRO AFP P. VITAL 358.116`,
`ESENCIAL ADICIONAL 27.853`, `LIQUIDO A PAGAR 2.983.237` -- all match the
CSV's `2026-02` row exactly, not `2025-02`). `2026-02` already reconciles
cleanly under the Session 4/5 fix (it falls inside the confirmed `5.30 UF`
tier, 2026-01..2026-02), so this PDF doesn't shed any new light on the
actual open `2025-02` (`$2,860`) residual -- that one is still only
supported by the proportion-based hypothesis from Session 5. If a genuine
Febrero **2025** payslip becomes available, it would be the direct way to
confirm or refute the ~February 24, 2025 partial-enrollment hypothesis.

### `202605.pdf` (Mayo 2026) fully resolves the `1 CLP` `net_pay` mystery -- root cause found, and it's neither pf-payroll nor pf-rates

The PDF's own discount breakdown for the complementary insurance concept is
split into 3 separate line items that the CSV's single `health_insurance`
column had combined into one number:

| Concept (PDF) | Amount |
|---|---|
| SEGURO DENTAL | 7,716 |
| SEGURO DE SALUD | 33,707 |
| SEGURO CATASTROFICO | 5,279 |
| **Real sum** | **46,702** |
| **CSV's declared `health_insurance` column** | **46,703** |

The CSV's combined figure is **1 CLP higher** than the true sum of the 3
real policy amounts on the actual payslip. Since `POST /payroll/import`
persists the CSV's declared `health_insurance` value verbatim as one
`PAY_ITEM` (it has no way to know it should have been `46,702` instead --
that's a pre-import data-entry rounding slip, not something derivable from
the file itself), the net_pay reconciliation (which sums *all* declared
`PAY_ITEM` amounts, this one included) comes out `1 CLP` lower than the
CSV's own separately-declared `net_pay_clp` field -- which, on the real
payslip, is correctly `4,040,847 - 905,869 = 3,134,978` (using the true
`46,702`), while re-summing the CSV's own declared columns (using its
slightly-off `46,703`) gives `3,134,977`.

**Root cause confirmed and closed:** this is a **1 CLP arithmetic slip in
the source CSV's `health_insurance` column** (someone added
`7,716 + 33,707 + 5,279` and got `46,703` instead of `46,702` when
preparing the file) -- **not** a rounding bug in
`ContributionCalculator`, income tax, or unemployment insurance as
Session 1 had speculated. The CSV is internally inconsistent by exactly
`1 CLP` between its own `health_insurance` column and its own `net_pay`
column; pf-payroll faithfully reflects that same `1 CLP` gap in its
reconciliation, which is exactly the *intended* behavior of the check (see
"Context" -- it flags real declared-data inconsistencies). No code or
reference-data fix applies here; the fix, if desired, would be correcting
the source CSV's `health_insurance` value to `46,702` for this one period.
Left as-is given the amount (`1 CLP`, already within every other
reconciliation tolerance boundary).

### Status after Session 6

- `2026-05` (`1 CLP`): **root cause found and confirmed** -- a `1 CLP`
  summing slip in the source CSV, unrelated to any pf-payroll/pf-rates
  code. Not worth correcting the historical CSV for `1 CLP`; documented and
  closed.
- `2025-02` (`$2,860`): **still open**, unchanged by this session -- the
  PDF received for this session was for the wrong period (`2026-02`
  instead of `2025-02`). Still needs the real February 2025 payslip (or
  equivalent) to confirm/refute the partial-enrollment hypothesis from
  Session 5.

## Session 7 (2026-09-26, same day): the real Febrero 2025 payslip confirms the amount, but adds no new precision -- closed as-is

The user supplied the actual `Febrero, 2025` payslip PDF (confirmed by its
own header). It states `ESENCIAL ADICIONAL: $2,860` explicitly -- exactly
the CSV's declared value. Unlike the `202605.pdf` case (Session 6), this
payslip's own totals are **internally perfectly consistent**: haberes
(`3,844,844`) minus descuentos (`818,422`) equals the declared `líquido a
pagar` (`3,026,422`) exactly, with every individual line item (including
`ESENCIAL ADICIONAL: 2,860`) summing cleanly -- no hidden rounding slip
like the `1 CLP` case.

**Conclusion:** `$2,860` is confirmed as a real, deliberate, correctly
calculated amount from Corporative's own payroll system for this employee's
February 2025 Isapre "additional" plan portion -- **not** a CSV
transcription error, and not internally inconsistent like the `2026-05`
case. It genuinely is lower than a full month at the `4.94 UF` tier. The
payslip contains no observation, note, or day-level breakdown that pins
down an exact enrollment effective date, so it neither confirms nor refutes
the specific `~February 24, 2025` estimate from Session 5's proportion
analysis -- it only confirms the number itself is real and intentional.

With no further line-item detail available on the payslip itself, this is
the practical ceiling of what can be determined from documents already in
hand. Closing this residual as: **real, understood in principle (a genuine
partial-month proration), not precisely reconstructable without an HR/Isapre
source for the exact enrollment date**, and -- as already established in
Session 5 -- not modelable by the current binary (non-prorated) plan
validity check regardless. No further investigation planned unless a new
source (HR records, the Isapre contract, or day-level proration support in
the domain model) becomes available.

### Status after Session 7 -- final

- `2026-05` (`1 CLP`): closed, root cause confirmed (Session 6) -- a
  summing slip in the source CSV.
- `2025-02` (`$2,860`): closed as an accepted, understood, non-actionable
  residual -- confirmed real and correctly declared (Session 7), consistent
  with a genuine partial-month plan enrollment that the current model
  cannot prorate (Session 5).
- All other 20 of the 22 real periods reconcile exactly.
- **No further action pending on this investigation.**

## Session 8 (2026-09-26, same day): the test fixture CSV was adapted so it imports clean

At the user's explicit request, `secrets/payroll-input.csv` (the local test
fixture used throughout this investigation) was edited so both residuals
stop conflicting on import. **Both edits are documented here precisely
because they diverge, in different ways, from the real payslips analyzed
above -- this file is a test fixture, not a production submission, and this
note exists so nobody mistakes it for one later.**

- **`2026-05` `health_insurance`: `46703` -> `46702`.** This one is a
  straightforward correction, not a divergence from reality -- Session 6
  proved `46703` was itself a `1 CLP` summing slip against the real
  payslip's own 3 line items. The fixture now matches the real payslip
  exactly.
- **`2025-02` `health_plan_additional`: `2860` -> `0`, and `net_pay`:
  `3026422` -> `3029282` (`+2860`, to keep the row's own internal
  haberes-descuentos arithmetic consistent).** This one **does** diverge
  from the real payslip -- Session 7 confirmed `$2,860` was the real,
  correctly-declared amount on the actual `Febrero 2025` payslip. The user
  was presented with this tradeoff explicitly (via `ask_user_question`) and
  chose to zero it out anyway, to get a fully clean fixture file rather than
  carry a period that will always require the not-yet-supported day-level
  proration to reconcile. This is a deliberate, informed choice for the
  *fixture*, not a claim that Isapre didn't really charge `$2,860` that
  month -- see Session 7 for the real number.

**Verified:** re-running `payroll import-payroll secrets/payroll-input.csv`
against the local database now **commits successfully** (`imported_periods:
22`, `imported_items: 396`, exit code `0`, zero warnings) -- every period's
`contribution_validation.warning` and `net_pay_warning` is `null`. The
remaining `±1 CLP` diffs on `2025-04`, `2025-07`, and `2026-08` (ordinary
Decimal rounding, not a new finding) stay silently within the `100 CLP`
tolerance, same as before.

## Session 9 (2026-09-27): day-level proration implemented -- the binary-model limitation from Session 5 no longer applies

Sessions 5 and 7 both closed `2025-02` as non-actionable specifically
because "plan validity is a binary check against a single reference date
... there is no day-level proration anywhere in the pipeline" (Session 5).
That is no longer true: `ContributionCalculator.health()` now takes every
plan assigned to a period and sums each one's `contracted_uf` weighted by
how many days of the period's calendar month its `valid_from`/`valid_to`
actually overlaps (`domain/health_plan_proration.py`), and
`get_health_plans_overlapping_month()` (replacing the old single-day
`get_valid_health_plans_for_date()`) assigns a plan to a period as soon as
it overlaps *any part* of that month, not just day 1.

**This does not, by itself, fix `2025-02`.** No `PAY_HLTH_PLAN` row exists
with a `valid_from` inside February 2025 for the mechanism to prorate --
the real `Base` plan enrollment row still starts `2025-03-01` (per Finding
1/2, already applied to Neon). Making `2025-02` reconcile would require
deliberately seeding a new reference-data row (or splitting the existing
one) with a `valid_from` somewhere in February -- Session 5's proportion
analysis already estimated `~February 24, 2025`, but that's an *estimate*
reverse-engineered from one month's ratio, not a confirmed HR/Isapre date,
and touching real reference data on an estimate is a judgment call left
for an explicit follow-up decision rather than assumed here.

Status: mechanism in place and unit-tested; `2025-02`'s specific residual
is unchanged and still requires either a confirmed enrollment date or an
explicit decision to seed the estimated one.
