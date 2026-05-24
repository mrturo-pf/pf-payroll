"""Application helpers for required exchange-rate lookups."""

from datetime import date
from decimal import Decimal

from payroll.application.errors import ExchangeRateNotFoundError
from payroll.application.ports.repositories import MarketDataRepository


async def resolve_required_exchange_rate(
    *,
    provided_value: Decimal | None,
    currency_code: str,
    rate_date: date,
    market_data_repository: MarketDataRepository,
) -> Decimal:
    """Returns an explicit rate or loads the persisted value required by the use case."""

    if provided_value is not None:
        return provided_value

    resolved_value = await market_data_repository.get_exchange_rate_value(currency_code, rate_date)
    if resolved_value is None:
        raise ExchangeRateNotFoundError(f"{currency_code} exchange rate for {rate_date.isoformat()} was not found.")
    return resolved_value
