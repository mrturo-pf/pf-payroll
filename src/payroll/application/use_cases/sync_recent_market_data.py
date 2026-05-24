"""Use case for syncing recent market data history."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from payroll.application.dto import (
    MarketDataSyncRequestDTO,
    RefreshRatesCommandDTO,
    SyncRecentMarketDataResultDTO,
)
from payroll.application.ports.rate_provider import (
    EconomicIndexProvider,
    FxRateProvider,
)
from payroll.application.ports.repositories import MarketDataRepository
from payroll.shared.constants import (
    DAILY_MARKET_RATE_CODES,
    MONTHLY_ECONOMIC_INDEX_CODES,
    MONTHLY_MARKET_RATE_CODES,
)

_LOOKBACK_DAYS = 365
_LOOKBACK_MONTHS = 12


@dataclass(slots=True)
class SyncRecentMarketData:
    """Sync missing recent market data entries using provider-backed fetches."""

    repository: MarketDataRepository
    fx_provider: FxRateProvider
    economic_index_provider: EconomicIndexProvider
    today_provider: Callable[[], date] = date.today

    async def execute(self) -> SyncRecentMarketDataResultDTO:
        """Handle execute."""
        today = self.today_provider()
        daily_dates = self._build_daily_dates(today)
        monthly_dates = self._build_monthly_dates(today)

        missing_exchange_rate_requests = await self._collect_missing_exchange_rates(
            daily_dates, monthly_dates
        )
        missing_economic_index_requests = await self._collect_missing_economic_indices(
            monthly_dates
        )
        return await self.execute_request(
            MarketDataSyncRequestDTO(
                exchange_rate_dates=missing_exchange_rate_requests,
                economic_index_periods=missing_economic_index_requests,
            )
        )

    async def execute_request(
        self, request: MarketDataSyncRequestDTO
    ) -> SyncRecentMarketDataResultDTO:
        """Synchronize the explicitly requested market-data gaps."""
        requested_exchange_rates = sum(
            len(rate_dates) for rate_dates in request.exchange_rate_dates.values()
        )
        requested_economic_indices = sum(
            len(periods) for periods in request.economic_index_periods.values()
        )

        upserted_exchange_rates = await self._sync_exchange_rates(
            request.exchange_rate_dates
        )
        upserted_economic_indices = await self._sync_economic_indices(
            request.economic_index_periods
        )

        return SyncRecentMarketDataResultDTO(
            requested_exchange_rates=requested_exchange_rates,
            requested_economic_indices=requested_economic_indices,
            upserted_exchange_rates=upserted_exchange_rates,
            upserted_economic_indices=upserted_economic_indices,
        )

    async def _collect_missing_exchange_rates(
        self,
        daily_dates: list[date],
        monthly_dates: list[date],
    ) -> dict[str, list[date]]:
        """Collect missing exchange-rate requests."""
        missing_requests: dict[str, list[date]] = {}

        for currency_code in DAILY_MARKET_RATE_CODES:
            existing_dates = set(
                await self.repository.list_exchange_rate_dates(
                    currency_code, daily_dates[0], daily_dates[-1]
                )
            )
            missing_requests[currency_code] = [
                rate_date
                for rate_date in daily_dates
                if rate_date not in existing_dates
            ]

        for currency_code in MONTHLY_MARKET_RATE_CODES:
            existing_dates = set(
                await self.repository.list_exchange_rate_dates(
                    currency_code, monthly_dates[0], monthly_dates[-1]
                )
            )
            missing_requests[currency_code] = [
                rate_date
                for rate_date in monthly_dates
                if rate_date not in existing_dates
            ]

        return missing_requests

    async def _collect_missing_economic_indices(
        self, monthly_dates: list[date]
    ) -> dict[str, list[tuple[int, int]]]:
        """Collect missing economic-index requests."""
        missing_requests: dict[str, list[tuple[int, int]]] = {}
        first_month = monthly_dates[0]
        last_month = monthly_dates[-1]

        for code in MONTHLY_ECONOMIC_INDEX_CODES:
            existing_periods = set(
                await self.repository.list_economic_index_periods(
                    code,
                    first_month.year,
                    first_month.month,
                    last_month.year,
                    last_month.month,
                )
            )
            missing_requests[code] = [
                (month_date.year, month_date.month)
                for month_date in monthly_dates
                if (month_date.year, month_date.month) not in existing_periods
            ]

        return missing_requests

    async def _sync_exchange_rates(
        self, requests_by_code: dict[str, list[date]]
    ) -> int:
        """Fetch and persist exchange-rate entries grouped by code."""
        upserted_exchange_rates = 0
        for currency_code, requested_dates in requests_by_code.items():
            if not requested_dates:
                continue
            exchange_rates = await self.fx_provider.fetch_rate_entries(
                currency_code, requested_dates
            )
            if not exchange_rates:
                continue
            refresh_result = await self.repository.refresh_rates(
                RefreshRatesCommandDTO(exchange_rates=exchange_rates)
            )
            upserted_exchange_rates += refresh_result.upserted_exchange_rates
        return upserted_exchange_rates

    async def _sync_economic_indices(
        self, requests_by_code: dict[str, list[tuple[int, int]]]
    ) -> int:
        """Fetch and persist economic-index entries grouped by code."""
        upserted_economic_indices = 0
        for code, requested_periods in requests_by_code.items():
            if not requested_periods:
                continue
            economic_indices = await self.economic_index_provider.fetch_indices(
                code, requested_periods
            )
            if not economic_indices:
                continue
            refresh_result = await self.repository.refresh_rates(
                RefreshRatesCommandDTO(economic_indices=economic_indices)
            )
            upserted_economic_indices += refresh_result.upserted_economic_indices
        return upserted_economic_indices

    def _build_daily_dates(self, today: date) -> list[date]:
        """Build daily dates for the rolling one-year window."""
        start_date = today - timedelta(days=_LOOKBACK_DAYS - 1)
        return [start_date + timedelta(days=offset) for offset in range(_LOOKBACK_DAYS)]

    def _build_monthly_dates(self, today: date) -> list[date]:
        """Build monthly dates for the rolling twelve-month window."""
        month_cursor = date(today.year, today.month, 1)
        monthly_dates: list[date] = []
        for _ in range(_LOOKBACK_MONTHS):
            monthly_dates.append(month_cursor)
            month_cursor = self._previous_month(month_cursor)
        monthly_dates.reverse()
        return monthly_dates

    def _previous_month(self, month_date: date) -> date:
        """Return the first day of the previous month."""
        if month_date.month == 1:
            return date(month_date.year - 1, 12, 1)
        return date(month_date.year, month_date.month - 1, 1)
