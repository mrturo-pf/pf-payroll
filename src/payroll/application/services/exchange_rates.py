"""Application helpers for required exchange-rate lookups."""

from datetime import date
from decimal import Decimal

from payroll.application.errors import ExchangeRateNotFoundError
from payroll.application.ports.repositories import MarketDataRepository
from payroll.shared.dates import last_day_of_month


async def resolve_required_exchange_rate(
    *,
    provided_value: Decimal | None,
    currency_code: str,
    rate_date: date,
    market_data_repository: MarketDataRepository,
) -> Decimal:
    """Return an explicit rate or load the persisted value required by the use case."""
    if provided_value is not None:
        return provided_value

    resolved_value = await market_data_repository.get_exchange_rate_value(
        currency_code, rate_date
    )
    if resolved_value is None:
        raise ExchangeRateNotFoundError(
            f"{currency_code} exchange rate for {rate_date.isoformat()} was not found."
        )
    return resolved_value


async def resolve_month_end_uf_exchange_rate(
    *,
    provided_value: Decimal | None,
    payment_date: date,
    market_data_repository: MarketDataRepository,
) -> Decimal:
    """Resolve the UF exchange rate for the month-end payment date."""
    return await resolve_required_exchange_rate(
        provided_value=provided_value,
        currency_code="UF",
        rate_date=last_day_of_month(payment_date),
        market_data_repository=market_data_repository,
    )
