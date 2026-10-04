# Batch lookups for pf-rates — recommendation

> This recommendation answers `pf-rates-batch-lookup-brief.md` after a direct,
> code-grounded investigation across `pf-rates`, `pf-payroll`, and `pf-sheets`. A
> stronger implementation-oriented draft already exists as
> `pf-rates-batch-lookup-plan.md`; this document's job is to make the design
> choices explicit, justify them against the real code, and confirm which parts of the
> original instruction do **not** survive contact with the actual codebase.

## 1. What is really happening today

### 1.1 `pf-payroll` has a real fan-out problem

`pf-payroll` consumes pf-rates through `PfRatesClient`
(`src/payroll/infrastructure/http/pf_rates_client.py`), which only exposes **singular**
lookups:

- `get_exchange_rate_value(currency_code, rate_date)` → `GET /exchange-rates/value`
- `get_economic_index_value(code, year, month)` → `GET /economic-indices/value`
- `get_latest_economic_index(code)` → `GET /economic-indices?code=...`

The multi-period payroll query path then fans those singular calls out in two layers:

1. `resolve_currency_equivalents()` resolves USD/EUR/UF for one period via one
   `asyncio.gather(...)` over the three currencies.
2. `list_period_ranges()` gathers that again across every non-future period in the
   window.

So for a concrete 3-period window needing USD/EUR/UF, `pf-payroll` performs **9 distinct
HTTP requests** to `pf-rates`. They are concurrent, so latency is softened, but the
request count is still 9. For the full default 13-period window (12 previous + current),
that scales to **39 distinct HTTP requests**.

That is the optimization target.

### 1.2 `pf-sheets` does **not** have the same problem

The original instruction assumed `pf-sheets` also needed to batch live per-value calls.
That assumption does not hold against the real code.

`pf-sheets`' `GET_CLP` / `GET_CLP_RANGE` path reads from the local `VALUES` sheet tab,
not from `pf-rates`' per-value HTTP endpoints. Those sheet values arrive through a
completely different flow:

1. `updateExchangeRates()` triggers `POST /exports/financial-data` on `pf-rates`.
2. `pf-rates` builds one combined CSV (exchange rates + economic indices) and uploads it
   to Drive.
3. `pf-sheets` reads that CSV from Drive, converts it to the `VALUES` tab format, and
   incrementally upserts the sheet rows.
4. `GET_CLP` / `GET_CLP_RANGE` then serve cells from that local snapshot.

So `pf-sheets` already solves "many values at once" with a **snapshot export** pattern,
not live pair lookups. There is no N+1 live HTTP problem there to fix.

## 2. What pf-rates already has internally

The nice plot twist: `pf-rates` already contains the core optimization logic this feature
needs — just not as a public endpoint.

### 2.1 Exchange-rate bulk resolution already exists internally

`ExportExchangeRatesCsv` already avoids one-query-per-date behavior by:

- bulk-fetching all stored values for one currency across a date range via
  `list_exchange_rate_values(code, start, end)`
- resolving requested dates in memory first
- only falling through to the full `GetExchangeRateValue` cascade when needed

That cascade is already the authoritative single-value behavior in `pf-rates`:

1. exact DB hit
2. exact provider fetch
3. nearest prior DB value (bounded lookback)
4. provider lookback probe
5. not found

### 2.2 Economic-index bulk resolution already exists internally too

`ExportCombinedFinancialDataCsv` already bulk-loads economic indices by code via
`list_economic_indices(code)` and resolves `(year, month)` values from that in-memory map.

### 2.3 Recommendation implication

The new batch endpoints should **reuse** those bulk-resolution patterns, not invent a
second algorithm. Otherwise we'd end up with three subtly different resolution behaviors:

- singular endpoint behavior
- CSV export behavior
- new batch endpoint behavior

That would be peak enterprise nonsense. Let's not.

## 3. Recommended endpoint contract

### 3.1 Add two new additive endpoints

Recommended endpoints:

- `POST /exchange-rates/values`
- `POST /economic-indices/values`

Keep the existing singular endpoints unchanged:

- `GET /exchange-rates/value`
- `GET /economic-indices/value`

This is additive, not a replacement.

### 3.2 Use `POST`, not `GET`

Reasoning:

- the request is a list of structured pairs, not a few scalar query params
- existing bulk-ish routes on these same resources already use `POST` + JSON body
  (`/refresh`)
- query-string encoding of dozens of pairs would be ugly, fragile, and length-limited
- there is no existing query-param convention in this codebase for "list of composite
  objects"

### 3.3 Request shape

Recommended request bodies:

```json
{
  "pairs": [
    { "currency_code": "USD", "rate_date": "2026-01-15" },
    { "currency_code": "EUR", "rate_date": "2026-01-15" },
    { "currency_code": "UF", "rate_date": "2026-02-15" }
  ]
}
```

```json
{
  "pairs": [
    { "code": "IPC_CL", "period_year": 2026, "period_month": 1 },
    { "code": "IPC_CL", "period_year": 2026, "period_month": 2 }
  ]
}
```

### 3.4 Response shape

Recommended response bodies:

```json
{
  "results": [
    { "currency_code": "USD", "rate_date": "2026-01-15", "value_clp": "950.1234" },
    { "currency_code": "EUR", "rate_date": "2026-01-15", "value_clp": null },
    { "currency_code": "UF", "rate_date": "2026-02-15", "value_clp": "38500.0000" }
  ]
}
```

```json
{
  "results": [
    { "code": "IPC_CL", "period_year": 2026, "period_month": 1, "index_value": "113.15" },
    { "code": "IPC_CL", "period_year": 2026, "period_month": 2, "index_value": null }
  ]
}
```

### 3.5 Why this shape

Use an **order-preserving list** that echoes each request item's identifying fields.

This is better than a keyed map because:

- no composite-key formatting convention to invent/version (`USD|2026-01-15`, etc.)
- duplicate pairs remain representable without weird client logic
- clients can reconstruct a dict if they want one
- the response is self-describing even if order is later ignored by a caller

## 4. Recommended degradation semantics

### 4.1 Missing data should degrade per pair, not fail the whole batch

For both new endpoints:

- an individually unresolvable pair returns `null` in its own result slot
- the batch request still succeeds overall
- true infrastructure/service failure still fails the request

This matches the real consumer behavior in `pf-payroll`: currency enrichments are already
independent best-effort values. One missing EUR rate should not erase valid USD and UF
results in the same request.

### 4.2 Why not fail fast on the first miss

Because the new batch endpoint is replacing many singular lookups that today already
resolve independently. A "one miss kills the batch" design would make the new endpoint
*less* useful and *less* faithful to current consumers than the inefficient old path.

## 5. Recommended internal algorithm

## 5.1 Exchange rates

For `POST /exchange-rates/values`:

1. group request pairs by `currency_code`
2. for each currency group:
   - compute `min(rate_date)` / `max(rate_date)`
   - bulk-fetch stored values once for that span
   - resolve each requested date using the same logic already used by the CSV export:
     cached exact → in-memory prior-date fallback where valid → full singular cascade
3. emit one result per input pair, preserving input order

### 5.2 Economic indices

For `POST /economic-indices/values`:

1. group request pairs by `code`
2. bulk-fetch `list_economic_indices(code)` once per distinct code
3. resolve each requested `(year, month)` from the in-memory map
4. emit one result per input pair, preserving input order

### 5.3 Concurrency recommendation

Do **not** naively `gather()` every unresolved exchange-rate pair all at once.

Why:

- singular exchange-rate resolution can hit live external providers
- a large miss-heavy batch could stampede BCCH/SII/Mindicador from one request
- `ExportExchangeRatesCsv` already documents sequential resolution as the safer choice

Recommended behavior:

- resolve **per currency group sequentially** inside `pf-rates`
- still gain the big win: one HTTP request from `pf-payroll` instead of N

This is the right trade-off: reduce inter-service call count aggressively, while staying
boring and safe inside the rate-resolution service itself.

## 6. Where the shared logic should live

Extract the reusable exchange-rate bulk-resolution pieces from
`ExportExchangeRatesCsv` into a shared internal module, e.g.
`rates/application/use_cases/_bulk_rate_resolution_shared.py`.

That module should own the common helpers used by:

- `ExportExchangeRatesCsv`
- the new `GetExchangeRateValues` use case

Why extract:

- DRY
- one source of truth for date-resolution behavior
- prevents CSV export and batch endpoint from drifting apart over time
- avoids jscpd grief, which this repo treats like a religion because apparently we enjoy
  discipline

For economic indices, reuse can stay lighter because `list_economic_indices(code)` is
already simple and the mapping logic is tiny.

## 7. Recommended pf-payroll changes

### 7.1 Extend the `MarketDataRepository` port

Add batched methods to `pf-payroll`'s own `MarketDataRepository` protocol:

- `get_exchange_rate_values(pairs)`
- `get_economic_index_values(pairs)`

Return shape recommendation:

- exchange rates → `dict[(currency_code, date)] -> Decimal | None`
- economic indices → `dict[(code, year, month)] -> Decimal | None`

That is the most convenient internal form for `pf-payroll`'s repository logic.

### 7.2 Extend `PfRatesClient`

Implement those new methods in `PfRatesClient` with **cache awareness**:

1. inspect the existing per-item TTL cache first
2. remove cache hits from the outbound batch request
3. call the new pf-rates batch endpoint only for misses
4. merge results back into the same per-item cache keys already used by the singular
   methods

That preserves today's cache semantics instead of creating a weird parallel cache lane.

### 7.3 Change only the call sites that actually benefit

#### Yes, batch these:

- `resolve_currency_equivalents()` / `list_period_ranges()` / `get_period_range()`
  because this is the real fan-out hotspot
- `deflate_amounts.py`, where two index lookups are naturally known together and can be
  collapsed into one request cheaply

#### No, leave these singular:

- `predict_next_period_net_pay()`
- `complementary_insurance_cost_computation.py`
- `project_future_months()` IPC logic

Why leave them alone:

- they do not fan out over many pairs
- some perform exactly one lookup
- `project_future_months()` already reuses `get_latest_economic_index()` efficiently
- forcing everything into batching would be ceremony without payoff

That is classic YAGNI territory. Just because we *can* batch something does not mean the
repo owes us that drama.

## 8. Recommended pf-sheets outcome

**No implementation changes recommended in `pf-sheets`.**

The brief's investigation already established that `pf-sheets` is not a consumer of the
singular lookup endpoints being optimized here. It uses a bulk export + local snapshot
architecture for an Apps Script reason, not by accident.

So the correct recommendation is:

- document that `pf-sheets` stays unchanged
- do **not** force a new dependency on live batch endpoints where none exists today
- only revisit this if the user separately wants a broader redesign of `GET_CLP`'s data
  source

## 9. Batch size limit recommendation

Recommended limit: **500 pairs per request** on both endpoints.

Why 500:

- there is already precedent in `pf-sheets`' `GET_CLP_RANGE`
- `pf-payroll`'s real demand is far lower (roughly 39 exchange-rate pairs and a handful
  of index pairs)
- it provides generous headroom without making one request unboundedly expensive
- simple Pydantic validation gives an immediate 422 for oversized requests

No need to get cute with a larger number until a real consumer proves it needs one.

## 10. Testing recommendation

### 10.1 pf-rates

Add tests proving:

- one bulk fetch per distinct exchange-rate currency group
- one bulk fetch per distinct economic-index code
- per-pair `null` degradation without whole-batch failure
- empty batch short-circuits without DB/provider work
- request-size cap returns 422
- extracted shared helpers do not change existing CSV export behavior

### 10.2 pf-payroll

Add/adjust tests proving:

- `PfRatesClient` only batch-fetches cache misses
- batch-fetched results populate the per-item cache correctly
- the `GET /payroll` / `get_period_range()` hotspot now performs **one** batched
  exchange-rate fetch instead of many singular ones
- singular call sites remain singular where intentionally unchanged

### 10.3 Most important regression test

Assert **call count**, not just values.

Without that, a future refactor could silently reintroduce N singular HTTP calls while
still returning all the correct decimals. That kind of regression is exactly the sort of
thing a too-happy green test suite loves to miss.

## 11. Rollout and compatibility

Recommended rollout order:

1. implement and deploy pf-rates new endpoints first
2. then implement and deploy pf-payroll consumption
3. no pf-sheets deploy needed

Compatibility stance:

- existing singular endpoints remain supported
- no `pf-db` migration needed
- no API breaking change required
- docs and Postman must be updated in the same implementation session

## 12. Cost impact

This change reduces cost rather than adding it:

- fewer inter-service HTTP calls from `pf-payroll` to `pf-rates`
- fewer repeated request-initialization cycles in Cloud Run
- no new infrastructure
- no new datastore
- no schema work

The main trade-off is slightly larger JSON payloads per batch request, which is trivial
compared with eliminating dozens of separate HTTP round-trips.

## 13. Final recommendation

Implement **two new additive batch endpoints in `pf-rates`**:

- `POST /exchange-rates/values`
- `POST /economic-indices/values`

Design them as:

- request body: `pairs: [...]`
- response body: `results: [...]`, same order, same identifying fields echoed back
- missing data: per-item `null`, not whole-batch failure
- size limit: 500 pairs
- exchange-rate resolution: reuse the existing bulk-fetch + fallback machinery already
  used by the CSV export, extracted into a shared internal helper module
- economic-index resolution: reuse `list_economic_indices(code)` + in-memory mapping

Then in `pf-payroll`:

- add batch methods to the `MarketDataRepository` port and `PfRatesClient`
- preserve the existing TTL cache semantics
- refactor the real hotspot (`resolve_currency_equivalents()` callers) to collect all
  needed pairs upfront and issue **one** batch request instead of many singular ones
- batch `deflate_amounts.py`'s paired index lookups too
- leave single-lookup and already-efficient call sites untouched

And in `pf-sheets`:

- do nothing

That last part is not laziness; it is the correct conclusion from the actual code.
The repo already solved that problem there, just through a different architecture.
