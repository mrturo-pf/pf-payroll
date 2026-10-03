"""HTTP adapter for the pf-rates financial data microservice."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import cast

from payroll.infrastructure.http._http_client import (
    PfRatesClientBase,
    pf_rates_get,
    pf_rates_get_list,
    pf_rates_post,
)
from payroll.shared.constants import MONTHLY_EXCHANGE_RATE_CODES


def _normalize_exchange_rate_date(currency_code: str, rate_date: date) -> date:
    """Normalize monthly-series currency codes to the first day of their month.

    pf-rates stores UTM exchange rates keyed to the first day of each month.
    Passing any day within that month must resolve to day-1 to match.
    """
    if currency_code.upper() in MONTHLY_EXCHANGE_RATE_CODES:
        return date(rate_date.year, rate_date.month, 1)
    return rate_date


class PfRatesClient(PfRatesClientBase):
    """HTTP adapter implementing MarketDataRepository via pf-rates REST API."""

    async def get_exchange_rate_value(
        self, currency_code: str, rate_date: date
    ) -> Decimal | None:
        """Return the CLP value for currency_code on rate_date, or None if absent."""
        normalized_date = _normalize_exchange_rate_date(currency_code, rate_date)
        cache_key = ("exchange_rate", currency_code, normalized_date)
        hit, cached = self._cache.get(cache_key)
        if hit:
            return cached  # type: ignore[return-value]

        result = await pf_rates_get(
            f"{self._base_url}/exchange-rates/value",
            {"currency_code": currency_code, "rate_date": normalized_date.isoformat()},
            self._headers,
            label="exchange rate",
        )
        value = Decimal(str(result["value_clp"])) if result is not None else None
        self._cache.set(cache_key, value)
        return value

    async def get_exchange_rate_values(
        self, pairs: list[tuple[str, date]]
    ) -> dict[tuple[str, date], Decimal | None]:
        """Return multiple exchange-rate values with per-pair cache reuse."""
        normalized_pairs = [
            (code, _normalize_exchange_rate_date(code, rate_date))
            for code, rate_date in pairs
        ]
        values: dict[tuple[str, date], Decimal | None] = {}
        missing: list[tuple[str, date]] = []
        seen_missing: set[tuple[str, date]] = set()
        for pair in normalized_pairs:
            cache_key = ("exchange_rate", pair[0], pair[1])
            hit, cached = self._cache.get(cache_key)
            if hit:
                values[pair] = cached
            elif pair not in seen_missing:
                missing.append(pair)
                seen_missing.add(pair)
        if missing:
            result = await pf_rates_post(
                f"{self._base_url}/exchange-rates/values",
                {
                    "pairs": [
                        {"currency_code": code, "rate_date": rate_date.isoformat()}
                        for code, rate_date in missing
                    ]
                },
                self._headers,
                label="exchange rate batch",
            )
            results = cast(list[dict[str, object]], result["results"] if result else [])
            for item in results:
                code = str(item["currency_code"])
                rate_date = date.fromisoformat(str(item["rate_date"]))
                raw_value = item.get("value_clp")
                value = Decimal(str(raw_value)) if raw_value is not None else None
                key = (code, rate_date)
                values[key] = value
                self._cache.set(("exchange_rate", code, rate_date), value)
            for pair in missing:
                values.setdefault(pair, None)
        return {
            (code, _normalize_exchange_rate_date(code, rate_date)): values.get(
                (code, _normalize_exchange_rate_date(code, rate_date))
            )
            for code, rate_date in pairs
        }

    async def get_economic_index_values(
        self, pairs: list[tuple[str, int, int]]
    ) -> dict[tuple[str, int, int], Decimal | None]:
        """Return multiple economic-index values with per-pair cache reuse."""
        values: dict[tuple[str, int, int], Decimal | None] = {}
        missing: list[tuple[str, int, int]] = []
        seen_missing: set[tuple[str, int, int]] = set()
        for pair in pairs:
            cache_key = ("economic_index", *pair)
            hit, cached = self._cache.get(cache_key)
            if hit:
                values[pair] = cached
            elif pair not in seen_missing:
                missing.append(pair)
                seen_missing.add(pair)
        if missing:
            result = await pf_rates_post(
                f"{self._base_url}/economic-indices/values",
                {
                    "pairs": [
                        {"code": code, "period_year": year, "period_month": month}
                        for code, year, month in missing
                    ]
                },
                self._headers,
                label="economic index batch",
            )
            results = cast(list[dict[str, object]], result["results"] if result else [])
            for item in results:
                key = (
                    str(item["code"]),
                    int(str(item["period_year"])),
                    int(str(item["period_month"])),
                )
                raw_value = item.get("index_value")
                value = Decimal(str(raw_value)) if raw_value is not None else None
                values[key] = value
                self._cache.set(("economic_index", *key), value)
            for pair in missing:
                values.setdefault(pair, None)
        return {pair: values.get(pair) for pair in pairs}

    async def get_economic_index_value(
        self, code: str, period_year: int, period_month: int
    ) -> Decimal | None:
        """Return the index value for code / year / month, or None if absent."""
        cache_key = ("economic_index", code, period_year, period_month)
        hit, cached = self._cache.get(cache_key)
        if hit:
            return cached  # type: ignore[return-value]

        result = await pf_rates_get(
            f"{self._base_url}/economic-indices/value",
            {"code": code, "year": period_year, "month": period_month},
            self._headers,
            label="economic index",
        )
        value = Decimal(str(result["index_value"])) if result is not None else None
        self._cache.set(cache_key, value)
        return value

    async def get_latest_economic_index(self, code: str) -> tuple[date, Decimal] | None:
        """Return the (period, value) most recently published for code, or None.

        GET /economic-indices?code=... already orders results by
        period_year/period_month descending, so the first item (if any) is
        the latest -- an empty list (200 OK, not 404) means the code has no
        stored values at all.
        """
        cache_key = ("latest_economic_index", code)
        hit, cached = self._cache.get(cache_key)
        if hit:
            return cached  # type: ignore[return-value]

        results = await pf_rates_get_list(
            f"{self._base_url}/economic-indices",
            {"code": code},
            self._headers,
            label="latest economic index",
        )
        value: tuple[date, Decimal] | None = None
        if results:
            latest = results[0]
            value = (
                date(
                    int(str(latest["period_year"])),
                    int(str(latest["period_month"])),
                    1,
                ),
                Decimal(str(latest["index_value"])),
            )
        self._cache.set(cache_key, value)
        return value
