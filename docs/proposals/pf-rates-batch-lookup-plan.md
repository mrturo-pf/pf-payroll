# Batch lookups for pf-rates (N rates × M dates, N indices × M dates) — design + implementation plan

> Status: **implementation in progress — pf-rates and pf-payroll slices complete locally; pf-sheets intentionally unchanged** — this is the living document the user asked
> for directly (skipping the usual brief → recommendation two-step dance from other
> proposals in this folder, since the open questions a recommendation would normally
> resolve were already settled during a live investigation session instead — see
> "How this was investigated" below). Implementation starts only after the user
> reviews this and confirms. Once started, this file gets updated in place with real
> findings/corrections, dated, per this folder's existing convention
> (`spreadsheet-export-plan.md`, `pdf-template-management-plan.md`).

## How this was investigated (not guessed)

This plan is grounded in two real conversations plus direct code reading across all
three repos, not speculation:

1. **The problem** was identified by walking `pf-payroll`'s actual call chain for
   `GET /payroll` (`resolve_currency_equivalents()`, `list_period_ranges()`,
   `get_period_range()`) line by line and counting real HTTP calls: for a 3-period
   window needing USD/EUR/UF, that is **9 separate HTTP GETs to pf-rates**, each a
   brand-new `httpx.AsyncClient()` (two levels of `asyncio.gather` fire them
   concurrently, so it's not 9 sequential round-trips, but it is still 9 distinct
   TCP/TLS/HTTP exchanges instead of 1).
2. **What pf-sheets actually does** was read directly (`GET_CLP`/`GET_CLP_RANGE`,
   `updateExchangeRates()`, `buildValuesCsvRows()`, `computeUpsertPlan()`) — it turns
   out pf-sheets **never calls pf-rates' per-value endpoints at all**. It triggers one
   bulk CSV export (`POST /exports/financial-data`), reads that CSV from Drive, and
   serves every cell lookup from a local sheet tab (`VALUES`) that export populated.
   This matters a lot for scope — see "pf-sheets: no changes needed" below.
3. **pf-rates' own export use cases were read directly** and turned out to already
   solve almost this exact problem internally, just not as a public, caller-driven
   endpoint:
   - `ExportExchangeRatesCsv.bulk_fetch_currency_values(code, start, end)` — one DB
     range query per currency instead of one query per date.
   - `ExportCombinedFinancialDataCsv._bulk_fetch_economic_index_values(code)` — one
     unfiltered DB query per index code, filtered in memory afterward.
   - Both docstrings already state, from a real production incident: *"resolving
     every date one at a time... does not scale: a multi-year window times several
     currencies means tens of thousands of sequential round-trips, comfortably enough
     to blow through both Cloud Run's request timeout and any corporate proxy in front
     of it — confirmed in production."* This proposal is the same lesson, applied to
     the public API surface instead of an internal export loop.
   - `MarketDataRepository.list_economic_index_periods(code, ranges: list[tuple[int,
     int]])` already uses SQLAlchemy's `tuple_(...).in_(...)` for a batched
     existence-check — direct, in-repo precedent for the exact query shape the new
     batch value lookups need, just extended to return values instead of existence.

## Problem statement (recap, with real numbers)

`pf-payroll` needs USD/EUR/UF exchange rates and IPC (economic index) values for many
`(currency-or-index, date)` pairs per single incoming request — e.g. `GET
/payroll?employer=X` resolving a 13-period window (12 previous + current), each needing
3 currencies, is up to **39 distinct exchange-rate lookups**, plus a future-months
projection needing IPC. `pf-rates` exposes only single-pair endpoints
(`GET /exchange-rates/value`, `GET /economic-indices/value`) — there is no way to ask
for many pairs in one HTTP call. `pf-payroll` currently works around this with
**concurrency** (`asyncio.gather`, two levels deep — see `payroll_repository_shared.py`
and `payroll_repository_queries.py`), which hides the *latency* cost but not the
*count* — it is still N separate HTTP requests, N separate pf-rates request-handling
cycles (auth check, dependency wiring, possible DB connection checkout), and N
separate opportunities for one single network hiccup to require its own independent
retry/backoff (see `_http_client.py`'s `_MAX_NETWORK_RETRY_ATTEMPTS` — a batch call
turns N independent retry budgets into 1).

## Decisions

### Decision 1 — two new endpoints, both additive, both `POST`

```text
POST /exchange-rates/values
POST /economic-indices/values
```

Existing `GET /exchange-rates/value` and `GET /economic-indices/value` are **not**
removed or changed — they stay for any single-value caller (manual testing, Postman,
future consumers that only ever need one value at a time, and the new batch use
case's own fallback path reuses `GetExchangeRateValue` internally anyway, see Decision
4). This is purely additive; zero breaking changes to the existing contract.

`POST`, not `GET`, even though this is a read: the request body is a list of
structured objects (not scalar query params), the existing codebase already uses
`POST` + list-body for bulk operations on these same two resources
(`POST /exchange-rates/refresh`, `POST /economic-indices/refresh` both take a list
body), and a list of dozens of `(code, date)` triples has no clean, precedent-backed
query-string encoding in this codebase. Consistent with existing convention beats
REST purism here.

**Request shape:**

```text
POST /exchange-rates/values
{
  "pairs": [
    { "currency_code": "USD", "rate_date": "2026-01-15" },
    { "currency_code": "EUR", "rate_date": "2026-01-15" },
    { "currency_code": "UF",  "rate_date": "2026-02-15" }
  ]
}

POST /economic-indices/values
{
  "pairs": [
    { "code": "IPC_CL", "period_year": 2026, "period_month": 1 },
    { "code": "IPC_CL", "period_year": 2026, "period_month": 2 }
  ]
}
```

**Response shape** — a list, same length and same order as the request's `pairs`,
each item echoing its own request fields plus the resolved value (or `null`):

```text
{
  "results": [
    { "currency_code": "USD", "rate_date": "2026-01-15", "value_clp": "950.1234" },
    { "currency_code": "EUR", "rate_date": "2026-01-15", "value_clp": null },
    { "currency_code": "UF",  "rate_date": "2026-02-15", "value_clp": "38500.0000" }
  ]
}
```

Chose an **order-preserving list that echoes its own inputs**, not a map keyed by a
composite string (e.g. `"USD|2026-01-15"`). Rationale: no key-formatting convention to
agree on/version (separator choice, case sensitivity, date format), and the client
builds its own lookup dict from the echoed fields in one line regardless — strictly
simpler contract, and echoing the inputs means the client never has to trust ordering
even though ordering is in fact preserved (belt and suspenders, free).

### Decision 2 — per-pair degradation, never a partial-batch failure

A pair that cannot be resolved (no DB row, provider has nothing, lookback window
exhausted) becomes `"value_clp": null` / `"index_value": null` in its own slot — it
does **not** fail the whole request. This exactly matches today's single-value
semantics already relied upon throughout `pf-payroll` (`resolve_currency_equivalents()`
already treats a missing currency as an independent, non-fatal `None` — see its own
docstring: *"a 3-way best-effort enrichment where each currency is independent of the
other two"*). The only thing that fails the whole batch request (mirroring
`to_http_exception` today) is pf-rates itself being unreachable/erroring at the
infrastructure level — same `PayrollDependencyError` propagation pattern already in
place for single calls.

### Decision 3 — cap batch size, mirroring an existing precedent

`pf-sheets`' own `GET_CLP_RANGE` already caps batch size (`GET_CLP_RANGE_MAX_PAIRS =
500`, `GET_CLP_RANGE_MAX_REQUEST_PAIRS = 5000`) for exactly this reason: bound
worst-case DB/provider load from one single HTTP request. Propose the same philosophy
for pf-rates' new endpoints: `pairs: list[...] = Field(max_length=500)` on both request
models (422 if exceeded) — comfortably above `pf-payroll`'s realistic ceiling (39 for
exchange rates, ~12 for indices) with headroom, while still bounding abuse.

### Decision 4 — internal resolution algorithm: extract and reuse, don't reinvent

Both new use cases reuse logic that already exists and is already proven in
production (`ExportExchangeRatesCsv`), rather than writing a second resolution
algorithm:

- **Exchange rates**: group the batch's pairs by `currency_code`. For each currency,
  call `bulk_fetch_currency_values(code, min_date, max_date)` once (covers the whole
  requested span for that currency in one DB query — same trade-off the export already
  accepts: a sparse set of dates spanning a year costs one query for that year, not
  one query per date, which is still strictly better than today's one-HTTP-call-per-
  date), then resolve each individual requested date via `resolve_value_for_date()`
  (in-memory first, falling through to `GetExchangeRateValue`'s DB/provider cascade
  only for genuine cache misses — unchanged fallback chain, see that class's own
  docstring for the exact 5-step order).
- **Economic indices**: group by `code`. For each code, call the existing
  `list_economic_indices(code)` once (already a full-series bulk fetch), then pick out
  just the requested `(year, month)` entries from the in-memory result. No DB shape
  change needed at all — this one is pure reuse, zero new query.
- **Currency/code groups are resolved sequentially, not concurrently** — deliberately
  mirroring `ExportExchangeRatesCsv`'s own documented reasoning: *"naturally
  rate-limit-friendly toward the external providers this use case may fall back to."*
  A batch with several currencies all missing the same date would otherwise fire a
  burst of concurrent provider calls from one request; sequential-per-group avoids
  that stampede. This is still a strict latency win for the caller vs. today, because
  it is one HTTP round-trip instead of N, even though pf-rates' own internal work is
  sequential.

**Code organization**: extract `bulk_fetch_currency_values()` and
`resolve_value_for_date()` (currently private-ish instance methods on
`ExportExchangeRatesCsv`) into a new shared module,
`rates/application/use_cases/_bulk_rate_resolution_shared.py`, following the exact
naming/privacy convention already established by `_export_csv_shared.py` (leading
underscore = internal to the `use_cases` package, shared by siblings). Both
`ExportExchangeRatesCsv` and the new `GetExchangeRateValues` batch use case depend on
this shared module — DRY, and guarantees the CSV export and the new batch endpoint can
never silently diverge in how they resolve a value.

New use cases (application layer, hexagonal):
- `rates/application/use_cases/get_exchange_rate_values.py` — `GetExchangeRateValues`
- `rates/application/use_cases/get_economic_index_values.py` — `GetEconomicIndexValues`

### Decision 5 — pf-sheets: no changes needed

Confirmed by direct code reading (see "How this was investigated" above): `GET_CLP`
and `GET_CLP_RANGE` read from the `VALUES` sheet tab, never from pf-rates' per-value
endpoints directly. The only pf-rates call pf-sheets ever makes is the existing
`POST /exports/financial-data` trigger, which is unrelated to and unaffected by this
proposal. There is no N+1 problem on the pf-sheets side to fix.

**Explicitly out of scope, flagged for the user rather than assumed:** migrating
`GET_CLP`'s real-time path away from the local `VALUES` snapshot and onto this new
batch endpoint directly would be a genuine *architecture change* (trading a
pre-synced local cache for a live per-request call to pf-rates), not an optimization
of existing behavior — and the local-snapshot design looks deliberate given Apps
Script's own custom-function sandbox constraints referenced in `webapp.js`'s own
comments (`SpreadsheetApp.openById()` being forbidden inside a custom function's
sandbox is *why* the Web-App-plus-local-sheet split exists at all). Not proposed here;
raise it separately if that is actually wanted.

### Decision 6 — pf-payroll: collect-then-batch, not a wholesale rewrite

New port methods on `pf-payroll`'s own `MarketDataRepository` Protocol
(`application/ports/repositories.py`):

```python
async def get_exchange_rate_values(
    self, pairs: list[tuple[str, date]]
) -> dict[tuple[str, date], Decimal | None]: ...

async def get_economic_index_values(
    self, pairs: list[tuple[str, int, int]]
) -> dict[tuple[str, int, int], Decimal | None]: ...
```

`PfRatesClient` implements both against the two new pf-rates endpoints, **cache-aware**:
before calling the batch endpoint, check the existing per-item `TTLCache` for each
requested pair and drop whatever is already cached; send only the real cache misses to
pf-rates; merge the fetched results back into the per-item cache (same granularity as
today, so a later single-value lookup elsewhere in the same request still gets a cache
hit) before returning the combined map. This preserves 100% of today's caching
semantics/tests — it only adds a new batched fetch path for the specific call sites
that actually fan out over many pairs.

**Call sites to change (the ones that actually fan out):**

- `resolve_currency_equivalents()` / `list_period_ranges()` / `get_period_range()`
  (`payroll_repository_shared.py`, `payroll_repository_queries.py`): currently, each
  period independently gathers its own 3 currency calls, and `list_period_ranges()`
  gathers all periods' calls together — but each of those "gathered" calls is still
  its own HTTP request. Change: collect **every** `(currency_code, date)` pair needed
  across the *entire* window up front (up to 13 periods × 3 currencies = up to 39
  pairs), call `get_exchange_rate_values()` **once**, then have
  `resolve_currency_equivalents()` read its 3 values out of the pre-fetched map
  instead of making its own `asyncio.gather` of individual calls. This is the actual
  fix: 39 HTTP requests (even if concurrent) collapse into exactly 1.
- `deflate_amounts.py`: two single, always-known-up-front index lookups (source period,
  target period) at the same call site — trivial, zero-risk win to batch these 2 into
  1 call via `get_economic_index_values()`, even though N=2 is small.

**Call sites deliberately left unchanged (YAGNI — flagging explicitly so nobody
"completes" this refactor where it buys nothing):**

- `predict_next_period_net_pay()` / `complementary_insurance_cost_computation.py`:
  each makes exactly one isolated UF lookup for one known date, not part of a larger
  fan-out. Batching a single value against itself has no payoff — keep using
  `get_exchange_rate_value()` (singular, unchanged).
- `project_future_months()`'s IPC step (`_apply_ipc_step()` /
  `_extrapolate_cycle_ratio()`): already near-optimal today — one
  `get_latest_economic_index()` call upfront, reused across the whole 12-month loop,
  plus at most **one** extra `get_economic_index_value()` call total (not one per
  month). Nothing to batch here.

### Decision 7 — regression test to actually prove the fix, not just that values resolve

The single most important new test across this whole proposal: assert the **call
count** to the batch client method drops to exactly 1 for a multi-period window (e.g.
`GET /payroll` over 3+ periods needing currency conversion), using a mock/spy that
fails the test if `get_exchange_rate_values()` is called more than once, or if the old
per-item `get_exchange_rate_value()` is called at all from that code path post-refactor.
Without this, a future change could silently regress back to N individual calls while
every other test (which only checks resolved *values*, not call *count*) stays green.

## Testing plan

**pf-rates (new):**
- `GetExchangeRateValues`/`GetEconomicIndexValues`: multiple distinct codes grouped
  correctly (one bulk fetch per distinct code, asserted via call count on a repository
  spy); a pair that cannot be resolved returns `null` in its slot without failing the
  rest of the batch; an empty `pairs` list returns an empty result with zero DB/provider
  calls; duplicate pairs in the same request are each resolved independently (no silent
  dedup that would complicate response-to-request zipping) but costs only one shared
  bulk-fetch per distinct code regardless of duplicate dates; cap enforcement (422 over
  `max_length=500`).
- `_bulk_rate_resolution_shared.py` extraction: existing `ExportExchangeRatesCsv` tests
  must stay green unmodified (pure extraction, no behavior change) — this is the
  regression guard that the extraction itself introduced zero drift.

**pf-payroll (updated/new):**
- `PfRatesClient` batch methods: cache hits are never re-requested from pf-rates;
  cache misses are merged back into the per-item cache after the batch call; a batch
  call with zero actual cache misses makes zero HTTP calls.
- `resolve_currency_equivalents()` / `list_period_ranges()` / `get_period_range()`:
  updated to assert against the new batched call shape; **the call-count regression
  test from Decision 7** is the new, load-bearing assertion here.
- Full existing suite (`make test-cov`, 100% coverage gate) must stay green —
  no regressions to resolved values, only to call shape.

## Implementation progress (2026-10-02)

### Completed locally

- **pf-rates**: added `POST /exchange-rates/values` and
  `POST /economic-indices/values`, capped at 500 pairs, preserving input order and
  degrading missing individual values to `null`.
- **pf-rates**: extracted shared bulk resolution helpers and reused them from the
  exchange-rate CSV export; existing export tests remain green.
- **pf-payroll**: added batch methods to the `MarketDataRepository` port and
  `PfRatesClient`, including per-pair TTL-cache reuse and one POST for cache misses.
- **pf-payroll**: refactored the period-range currency enrichment to collect all
  `(currency, date)` pairs and call the batch client once. Existing test doubles remain
  backward-compatible while adopting the new port is rolled out.
- **pf-payroll**: changed `DeflateAmounts` to request its two IPC values as one batch.
- **Docs/Postman**: updated `pf-rates/docs/api.md` and the ecosystem Postman collection.
- **Quality gates**: pf-rates unit suite: 212 passed; pf-payroll suite: 562 passed with
  100% coverage; mypy/ruff clean; both repositories' duplicate-code gates report 0
  clones. Docker-dependent pf-rates integration tests remain unavailable in this local
  environment because the Docker socket mount fails, independently of this change.

### Intentionally not changed

- **pf-sheets**: no source change. Its real flow is already one combined CSV export to
  Drive plus local `VALUES` snapshot reads; it does not call the singular pf-rates value
  endpoints and therefore has no N+1 path to optimize.
- Singular pf-payroll callers that perform only one lookup remain on the existing
  singular methods.


Purely additive on the pf-rates side (two new endpoints, zero changes to existing
ones) — no coordinated deploy ordering required beyond the normal
`pf-rates` → `pf-payroll` sequence any new consumed endpoint already implies (the
pf-payroll side simply cannot call an endpoint that doesn't exist yet in a deployed
pf-rates). No `pf-db` schema change — both new endpoints read existing
`RAT_EXCH_RATE`/`RAT_ECON_INDEX` tables through existing repository patterns, no new
migration needed.

## Cost impact (per AGENTS.md's mandatory cost-optimization section)

No new infrastructure, no new managed services, no schema change. Net effect is
**fewer** Cloud Run request invocations and fewer inter-service HTTP round-trips per
`pf-payroll` request (up to 39 collapse to 1 for the currency case) — a pure cost and
latency win, not a cost risk. The only new resource cost is marginally larger request
payloads (a list of ~39 small JSON objects vs. 39 tiny query strings) — negligible.

## Docs / Postman updates (same session as implementation, per the mandatory-tracking rule)

- `modules/pf-rates/docs/api.md`: document both new endpoints (request/response shape,
  auth, cap, degradation semantics) in the same style as the existing
  `GET /exchange-rates/value` / `GET /economic-indices/value` entries.
- `modules/pf-payroll/docs/api.md`: no public pf-payroll endpoint shape changes from
  this work (it's all internal to how `pf-payroll` talks to `pf-rates`) — confirm this
  explicitly once implemented, so this section isn't silently skipped.
- `postman/pf-ecosystem.postman_collection.json`: add the two new pf-rates requests
  under pf-rates' existing folder structure, with example bodies.

## Open questions for the user (please confirm before implementation starts)

1. Endpoint naming: `POST /exchange-rates/values` / `POST /economic-indices/values`
   (plural of the existing `/value` singular) — any objection, or a preferred name?
2. Batch cap of 500 pairs per request (Decision 3) — fine, or should it be lower/higher?
3. Confirm Decision 5 (pf-sheets: no changes) matches your intent — i.e. you were not
   actually asking to move `GET_CLP`'s real-time path off the local `VALUES` snapshot
   and onto live pf-rates calls.
4. OK to proceed with the `_bulk_rate_resolution_shared.py` extraction inside pf-rates
   (Decision 4) as part of this same change, even though it touches
   `ExportExchangeRatesCsv` (already-shipped, working code) purely to deduplicate
   logic the new batch endpoint needs too?

## Change log

- **2026-10-02** — Plan written after a live investigation session (no synthetic
  numbers): read `pf-payroll`'s actual `resolve_currency_equivalents()`/
  `list_period_ranges()` call chain, `pf-sheets`' actual `GET_CLP`/`GET_CLP_RANGE`/
  `updateExchangeRates()` implementation, and `pf-rates`' actual
  `ExportExchangeRatesCsv`/`ExportCombinedFinancialDataCsv` bulk-fetch internals.
  Not yet implemented — awaiting user confirmation on the open questions above.
