## Context

`pf-payroll` needs USD/EUR/UF exchange-rate values and `IPC_CL` economic-index values
for many `(currency-or-index, date)` pairs within a single incoming HTTP request, but
`pf-rates` only exposes single-pair endpoints:

```python
# modules/pf-rates/src/rates/interfaces/api/routes/exchange_rates.py
@router.get("/value")
async def get_exchange_rate_value(
    currency_code: str = Query(...),
    rate_date: date = Query(...),
    ...
) -> dict[str, str]: ...

# modules/pf-rates/src/rates/interfaces/api/routes/economic_indices.py
@router.get("/value")
async def get_economic_index_value(
    code: str = Query(...), year: int = Query(...), month: int = Query(...), ...
) -> dict[str, str]: ...
```

`pf-payroll`'s `PfRatesClient` (`infrastructure/http/pf_rates_client.py`) calls these
one pair at a time, and `resolve_currency_equivalents()`
(`infrastructure/db/repositories/payroll_repository_shared.py`) fans out over the 3
currencies it needs per period via `asyncio.gather`, while `list_period_ranges()`
(`infrastructure/db/repositories/payroll_repository_queries.py`) fans that out again
over every period in the window (up to 13: 12 previous + current). **Traced and
counted directly, not estimated:** a 3-period window needing USD/EUR/UF is **9
separate HTTP requests to pf-rates** — concurrency hides the *latency* (two nested
`asyncio.gather` levels fire them together) but not the *count*: 9 distinct
TCP/TLS/HTTP exchanges, 9 independent auth/dependency-wiring cycles on the pf-rates
side, 9 independent retry budgets (`_http_client.py`'s
`_MAX_NETWORK_RETRY_ATTEMPTS = 3`) if any single one hits a network blip.

## The instruction (translated/adapted into this codebase's terms)

I want `pf-rates` to expose, in a single call, the values of N rates for M indicated
dates — and a second single call for N indices across M indicated dates. Then make the
corresponding implementations in both `pf-payroll` and `pf-sheets` so that calls become
more efficient whenever many values are needed at once.

## Blind spots in the original instruction (confirmed/resolved against this codebase)

- **`pf-sheets` does not need this at all — confirmed by reading its actual code, not
  assumed.** `GET_CLP`/`GET_CLP_RANGE` (`interfaces/library.js`,
  `interfaces/webapp.js`) never call `pf-rates`' per-value endpoints; they read from a
  local `VALUES` sheet tab that is populated once by `updateExchangeRates()` triggering
  `POST /exports/financial-data` (a *combined bulk CSV export*, already existing,
  already batched on `pf-rates`' side) and parsing that CSV into the sheet
  (`buildValuesCsvRows()`, `computeUpsertPlan()`, `src/domain/index.js`). The
  instruction's premise — that `pf-sheets` makes many individual calls that could be
  batched — does not hold against the real code. Whatever this brief leads to, there
  is no `pf-sheets` N+1 problem to fix.
- **No response shape, HTTP method, or batch-size limit specified.** The instruction
  says "a single call" but not: is a missing value inside the batch a hard failure for
  the whole request, or a per-item `null`? Is the response a list or a map? Is there a
  cap on how many pairs one request can carry? `pf-sheets`' own `GET_CLP_RANGE`
  (`interfaces/webapp.js` lines 100-116) already had to answer exactly these questions
  for its own, unrelated batch surface (`GET_CLP_RANGE_MAX_PAIRS = 500`,
  `GET_CLP_RANGE_MAX_REQUEST_PAIRS = 5000`) — a direct, in-repo precedent worth
  reusing rather than inventing fresh conventions.
- **Exchange-rate resolution is not a pure DB read — it has a live provider-fallback
  cascade the instruction doesn't account for.** `GetExchangeRateValue.execute()`
  (`rates/application/use_cases/get_exchange_rate_value.py`) is a 5-step chain (DB
  exact hit → provider exact fetch → DB nearest-prior within
  `MAX_PROVIDER_LOOKBACK_DAYS = 7` days → provider probed for each of those 7 prior
  days → not found). A batch of many pairs that all miss cache simultaneously could,
  naively, fire a burst of concurrent calls to external providers (BCCH/SII/Mindicador)
  from a single incoming HTTP request — a stampede risk the instruction does not
  mention and that a naive "just loop and gather" implementation would introduce.
  Economic indices, by contrast, have **no** such cascade today
  (`GET /economic-indices/value` is a straight DB read, 404 if missing) — the two new
  endpoints are not symmetric in how much resolution logic they carry, and any design
  must account for that asymmetry rather than assume one algorithm covers both.
- **A very similar bulk-fetch problem has already been solved once in this exact
  codebase, for a different caller.** `ExportExchangeRatesCsv.bulk_fetch_currency_values()`
  and `ExportCombinedFinancialDataCsv._bulk_fetch_economic_index_values()`
  (`rates/application/use_cases/export_exchange_rates_csv.py`,
  `export_combined_financial_data_csv.py`) already fetch a currency's/index's whole
  needed span in one DB query instead of one query per date — with a docstring citing
  a real production incident: *"resolving every date one at a time... does not scale:
  a multi-year window times several currencies means tens of thousands of sequential
  round-trips, comfortably enough to blow through both Cloud Run's request timeout and
  any corporate proxy in front of it — confirmed in production."* Any new design
  should reuse this, not reinvent it — but the existing code solves it for a
  *contiguous rolling window* (lookback/forward days from today); `pf-payroll`'s real
  need is a *sparse, caller-specified set* of specific payment dates, not a window —
  confirm whether the existing bulk-fetch shape (`list_exchange_rate_values(code,
  start, end)`, a range query) still applies cleanly to a sparse set (it does — a
  range query covering `min(dates)..max(dates)` costs one query regardless of how
  sparse the actual requested dates are within that span — but this should be stated
  explicitly, not assumed silently).

## Additional constraints specific to this codebase (not in the original instruction)

- **Hexagonal architecture applies to both new endpoints.** Per `pf-rates/AGENTS.md`:
  `interfaces → application → domain`, ports as `Protocol`, DTOs as the only thing
  crossing layer boundaries. New use cases (`GetExchangeRateValues`,
  `GetEconomicIndexValues`) belong in `rates/application/use_cases/`, not inlined into
  the route handlers the way a quick script might.
- **DRY against the existing export use cases.** `bulk_fetch_currency_values()` /
  `resolve_value_for_date()` currently live as instance methods on
  `ExportExchangeRatesCsv` — a new batch-lookup use case needing the same logic must
  not duplicate it (this repo enforces a zero-duplication `jscpd` gate in CI, both
  locally and repo-wide — confirmed painfully in a recent session where a stray
  Markdown code fence tripped the repo-wide gate). Any recommendation must say exactly
  where this shared logic should live.
- **`pf-payroll`'s own `MarketDataRepository` port
  (`application/ports/repositories.py`) and its `PfRatesClient`
  (`infrastructure/http/pf_rates_client.py`) both need new methods to actually consume
  the new endpoints** — this is not just a `pf-rates` change. `PfRatesClient` also
  already has a per-item `TTLCache` (`infrastructure/http/_ttl_cache.py`,
  `cache_ttl_seconds` default 300s) — a new batch method must not bypass or break that
  caching for callers that still use the singular `get_exchange_rate_value()` /
  `get_economic_index_value()` methods elsewhere (e.g. `predict_next_period_net_pay()`,
  `complementary_insurance_cost_computation.py`, both single-value call sites that gain
  nothing from batching and should not be forced into it — YAGNI).
- **Not every `pf-payroll` call site that touches `pf-rates` actually has a fan-out
  problem.** `project_future_months()`'s IPC step (`_apply_ipc_step()` /
  `_extrapolate_cycle_ratio()`, same file) already fetches
  `get_latest_economic_index()` once upfront and reuses it across all 12 future
  months, with at most one extra `get_economic_index_value()` call total for the whole
  window — any recommendation should explicitly identify which call sites actually
  benefit from batching (the currency-equivalents fan-out, clearly) versus which do
  not (this one), rather than batching indiscriminately everywhere `pf-rates` is
  called.
- **Docs and Postman must track reality in the same change, not later** — this root
  repo's own `AGENTS.md` is explicit that letting `docs/api.md` (both `pf-rates`'s and,
  if applicable, `pf-payroll`'s) or `postman/pf-ecosystem.postman_collection.json`
  drift from a shipped endpoint change counts as an incomplete change, not a
  follow-up.
- **Cost-optimization is a mandatory section for anything infra-adjacent** — this root
  `AGENTS.md` requires every infra-touching proposal to state its cost impact and
  cheaper alternatives considered, even when (as expected here) the net effect is
  **fewer** Cloud Run invocations/HTTP round-trips, not more infrastructure.

## What I need

A design recommendation resolving:

1. **Endpoint contract.** Exact request/response shape for both new endpoints (list vs.
   map response, per-pair echo of inputs or not, HTTP method and why), and the
   per-pair degradation rule (does one unresolvable pair fail the whole batch, or does
   it degrade to `null` in its own slot, consistent with how
   `resolve_currency_equivalents()` already treats a missing single currency today?).
2. **Batch-size cap**, with a concrete number and rationale (precedent:
   `GET_CLP_RANGE`'s 500/5000 caps in `pf-sheets`).
3. **Internal resolution algorithm for both endpoints**, explicitly addressing the
   provider-fallback-cascade/stampede risk for exchange rates (sequential-per-currency-
   group, mirroring `ExportExchangeRatesCsv`'s own documented reasoning, or some other
   approach — state and justify) and confirming economic indices need no equivalent
   guard (no provider cascade exists for them today).
4. **Where the shared bulk-resolution logic lives** (new shared module vs. extending
   an existing one), so the new use cases and `ExportExchangeRatesCsv` do not duplicate
   it and trip the zero-duplication gate.
5. **`pf-payroll` call-site plan**, explicit about which call sites change
   (`resolve_currency_equivalents()`/`list_period_ranges()`/`get_period_range()`,
   `deflate_amounts.py`) and which are deliberately left alone
   (`predict_next_period_net_pay()`, `complementary_insurance_cost_computation.py`,
   `project_future_months()`'s IPC step) — with the reasoning for each, not a blanket
   "batch everything."
6. **`pf-sheets` scope**, stating explicitly that no change is needed there given its
   actual architecture (CSV export + local `VALUES` snapshot), and flagging — without
   assuming an answer — whether the user actually wants a *separate*, larger
   architectural change (migrating `GET_CLP`'s real-time path off the local snapshot
   and onto live `pf-rates` calls) instead.
7. **A regression test strategy that proves the call-*count* dropped**, not just that
   resolved values stayed correct — a mocked/spied call-count assertion, since every
   other existing test only checks values and would stay green even if a future change
   silently reintroduced the per-item fan-out.
8. **Rollout / backward compatibility**, confirming the existing single-value
   endpoints stay untouched and this is purely additive, plus whether any `pf-db`
   coordination is needed (expected: no, both endpoints read existing
   `RAT_EXCH_RATE`/`RAT_ECON_INDEX` tables — confirm explicitly rather than assume).

## Response format

- **Deliverable:** a new Markdown file in this same folder
  (`docs/proposals/pf-rates-batch-lookup-recommendation.md`).
- Ground every claim in the actual code (`pf_rates_client.py`, `_http_client.py`,
  `_ttl_cache.py`, `payroll_repository_shared.py`, `payroll_repository_queries.py`,
  `export_exchange_rates_csv.py`, `export_combined_financial_data_csv.py`,
  `market_data_repository.py` (port + infra), `exchange_rates.py`/`economic_indices.py`
  routes, `pf-sheets`' `webapp.js`/`library.js`/`domain/index.js`) the way prior
  recommendations in this folder have — cite file/line-level evidence, don't assume.
- **Note for whoever picks this up:** a first pass at exactly this recommendation's
  job was already produced directly (skipping the brief step, since the investigation
  that normally justifies a brief had already happened live in conversation) as
  `pf-rates-batch-lookup-plan.md` in this same folder. This brief was written
  retroactively, after that plan, specifically so this proposal's paper trail matches
  this folder's established three-document convention (brief → recommendation → plan)
  for anyone reading the history later. Treat the existing plan as a strong draft
  answer to every question above, not as something to ignore — but verify each of its
  8 decisions against this brief's blind spots rather than rubber-stamping it, since it
  was written before this brief's blind spots were enumerated this explicitly.

## Stack

- Language: Python (FastAPI, hexagonal) for `pf-rates`; Python (FastAPI, hexagonal)
  for `pf-payroll`'s consuming side; JavaScript/Apps Script for `pf-sheets` (expected:
  no changes, see blind spots above).
- Touches: `rates/interfaces/api/routes/{exchange_rates,economic_indices}.py`,
  `rates/application/use_cases/` (new use cases + possible shared-logic extraction),
  `rates/application/ports/market_data_repository.py` (if a new port method is
  justified), `payroll/application/ports/repositories.py`,
  `payroll/infrastructure/http/pf_rates_client.py`,
  `payroll/infrastructure/db/repositories/{payroll_repository_shared,
  payroll_repository_queries}.py`, `payroll/application/use_cases/deflate_amounts.py`.
- No `pf-db` schema change expected — both new endpoints read existing
  `RAT_EXCH_RATE`/`RAT_ECON_INDEX` tables through existing repository patterns; flag
  explicitly in the recommendation if that turns out to be wrong.
- No new external dependency/infrastructure: pure additive API surface over data
  `pf-rates` already stores/serves.
