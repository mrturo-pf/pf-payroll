"""HTTP adapter for the pf-rates financial data microservice."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from payroll.infrastructure.http._http_client import PfRatesClientBase, pf_rates_get
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
