"""Tests for syncing recent market data."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from payroll.application.dto import (
    EconomicIndexWriteDTO,
    ExchangeRateWriteDTO,
    MarketDataSyncRequestDTO,
    RefreshRatesCommandDTO,
    RefreshRatesResultDTO,
    SyncRecentMarketDataResultDTO,
)
from payroll.application.use_cases.sync_recent_market_data import SyncRecentMarketData
from helpers.market_data_stubs import MarketDataNotUsedMixin


class StubMarketDataRepository(MarketDataNotUsedMixin):
    """Test double for market-data repository."""

    def __init__(
        self,
        existing_exchange_rate_dates: dict[str, set[date]] | None = None,
        existing_index_periods: dict[str, set[tuple[int, int]]] | None = None,
    ) -> None:
        """Initialize the instance."""
        self.existing_exchange_rate_dates = existing_exchange_rate_dates or {}
        self.existing_index_periods = existing_index_periods or {}
        self.refresh_calls: list[RefreshRatesCommandDTO] = []

    async def get_exchange_rate_value(
        self, currency_code: str, rate_date: date
    ) -> Decimal | None:
        """Get exchange rate value."""
        raise AssertionError("not used")

    async def get_economic_index_value(
        self, code: str, period_year: int, period_month: int
    ) -> Decimal | None:
        """Get economic index value."""
        raise AssertionError("not used")

    async def list_exchange_rate_dates(
        self, currency_code: str, start_date: date, end_date: date
    ) -> list[date]:
        """List exchange rate dates."""
        return sorted(
            rate_date
            for rate_date in self.existing_exchange_rate_dates.get(currency_code, set())
            if start_date <= rate_date <= end_date
        )

    async def list_economic_index_periods(
        self,
        code: str,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
    ) -> list[tuple[int, int]]:
        """List economic index periods."""
        start_key = start_year * 100 + start_month
        end_key = end_year * 100 + end_month
        return sorted(
            period
            for period in self.existing_index_periods.get(code, set())
            if start_key <= period[0] * 100 + period[1] <= end_key
        )

    async def refresh_rates(
        self, command: RefreshRatesCommandDTO
    ) -> RefreshRatesResultDTO:
        """Refresh rates."""
        self.refresh_calls.append(command)
        for item in command.exchange_rates:
            self.existing_exchange_rate_dates.setdefault(item.currency_code, set()).add(
                item.rate_date
            )
        for item in command.economic_indices:
            self.existing_index_periods.setdefault(item.code, set()).add(
                (item.period_year, item.period_month)
            )
        return RefreshRatesResultDTO(
            upserted_exchange_rates=len(command.exchange_rates),
            upserted_economic_indices=len(command.economic_indices),
        )


class StubFxProvider:
    """Test double for FX provider."""

    def __init__(self, missing_requests: set[tuple[str, date]] | None = None) -> None:
        """Initialize the instance."""
        self.missing_requests = missing_requests or set()
        self.requests: list[tuple[str, date]] = []

    async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
        """Handle fetch rate."""
        entry = await self.fetch_rate_entry(currency_code, on)
        return None if entry is None else entry.value_clp

    async def fetch_rate_entry(
        self, currency_code: str, rate_date: date
    ) -> ExchangeRateWriteDTO | None:
        """Handle fetch rate entry."""
        self.requests.append((currency_code, rate_date))
        if (currency_code, rate_date) in self.missing_requests:
            return None
        return ExchangeRateWriteDTO(
            currency_code=currency_code,
            rate_date=rate_date,
            value_clp=Decimal("100.00"),
            source="provider",
        )

    async def fetch_rate_entries(
        self, currency_code: str, requested_dates: list[date]
    ) -> list[ExchangeRateWriteDTO]:
        """Handle fetch rate entries."""
        entries: list[ExchangeRateWriteDTO] = []
        for requested_date in requested_dates:
            entry = await self.fetch_rate_entry(currency_code, requested_date)
            if entry is not None:
                entries.append(entry)
        return entries


class StubEconomicIndexProvider:
    """Test double for economic-index provider."""

    def __init__(
        self, missing_requests: set[tuple[str, int, int]] | None = None
    ) -> None:
        """Initialize the instance."""
        self.missing_requests = missing_requests or set()
        self.requests: list[tuple[str, int, int]] = []

    async def fetch_indices(
        self, code: str, requested_periods: list[tuple[int, int]]
    ) -> list[EconomicIndexWriteDTO]:
        """Handle fetch indices."""
        entries: list[EconomicIndexWriteDTO] = []
        for period_year, period_month in requested_periods:
            entry = await self.fetch_index(code, period_year, period_month)
            if entry is not None:
                entries.append(entry)
        return entries

    async def fetch_index(
        self, code: str, period_year: int, period_month: int
    ) -> EconomicIndexWriteDTO | None:
        """Handle fetch index."""
        self.requests.append((code, period_year, period_month))
        if (code, period_year, period_month) in self.missing_requests:
            return None
        return EconomicIndexWriteDTO(
            code=code,
            period_year=period_year,
            period_month=period_month,
            index_value=Decimal("112.340000"),
            monthly_change=Decimal("0.7000"),
            yearly_change=Decimal("4.1000"),
            base_period="2023=100",
            source="provider",
        )


_APRIL_2026_MARKET_DATA_REQUEST = MarketDataSyncRequestDTO(
    exchange_rate_dates={
        "UF": [date(2026, 4, 29)],
        "UTM": [date(2026, 4, 1)],
    },
    economic_index_periods={"IPC_CL": [(2026, 4)]},
)


@pytest.mark.asyncio
async def test_sync_recent_market_data_fetches_only_missing_entries() -> None:
    """Test syncing only fetches and inserts missing entries."""
    repository = StubMarketDataRepository(
        existing_exchange_rate_dates={
            "USD": {date(2026, 5, 8)},
            "UTM": {date(2026, 5, 1)},
        },
        existing_index_periods={"IPC_CL": {(2026, 5)}},
    )
    fx_provider = StubFxProvider()
    economic_index_provider = StubEconomicIndexProvider()
    use_case = SyncRecentMarketData(
        repository,
        fx_provider,
        economic_index_provider,
        today_provider=lambda: date(2026, 5, 8),
    )

    result = await use_case.execute()

    assert result == SyncRecentMarketDataResultDTO(
        requested_exchange_rates=1105,
        requested_economic_indices=11,
        upserted_exchange_rates=1105,
        upserted_economic_indices=11,
    )
    assert len(repository.refresh_calls) == 5
    assert sum(len(call.exchange_rates) for call in repository.refresh_calls) == 1105
    assert sum(len(call.economic_indices) for call in repository.refresh_calls) == 11
    assert ("USD", date(2026, 5, 8)) not in fx_provider.requests
    assert ("UTM", date(2026, 5, 1)) not in fx_provider.requests
    assert ("IPC_CL", 2026, 5) not in economic_index_provider.requests


@pytest.mark.asyncio
async def test_sync_recent_market_data_skips_insert_when_nothing_is_missing() -> None:
    """Test syncing skips inserts when the window is already complete."""
    today = date(2026, 1, 5)
    start_date = today - timedelta(days=364)
    daily_dates = {start_date + timedelta(days=offset) for offset in range(365)}
    monthly_dates: set[date] = set()
    month_cursor = date(today.year, today.month, 1)
    for _ in range(12):
        monthly_dates.add(month_cursor)
        if month_cursor.month == 1:
            month_cursor = date(month_cursor.year - 1, 12, 1)
        else:
            month_cursor = date(month_cursor.year, month_cursor.month - 1, 1)
    repository = StubMarketDataRepository(
        existing_exchange_rate_dates={
            "USD": set(daily_dates),
            "EUR": set(daily_dates),
            "UF": set(daily_dates),
            "UTM": set(monthly_dates),
        },
        existing_index_periods={
            "IPC_CL": {
                (month_date.year, month_date.month) for month_date in monthly_dates
            }
        },
    )
    use_case = SyncRecentMarketData(
        repository,
        StubFxProvider(),
        StubEconomicIndexProvider(),
        today_provider=lambda: today,
    )

    result = await use_case.execute()

    assert result == SyncRecentMarketDataResultDTO(
        requested_exchange_rates=0,
        requested_economic_indices=0,
        upserted_exchange_rates=0,
        upserted_economic_indices=0,
    )
    assert repository.refresh_calls == []


@pytest.mark.asyncio
async def test_sync_recent_market_data_ignores_provider_misses() -> None:
    """Test syncing ignores missing provider responses."""
    repository = StubMarketDataRepository()
    use_case = SyncRecentMarketData(
        repository,
        StubFxProvider(missing_requests={("USD", date(2026, 5, 8))}),
        StubEconomicIndexProvider(missing_requests={("IPC_CL", 2026, 5)}),
        today_provider=lambda: date(2026, 5, 8),
    )

    result = await use_case.execute()

    assert result == SyncRecentMarketDataResultDTO(
        requested_exchange_rates=1107,
        requested_economic_indices=12,
        upserted_exchange_rates=1106,
        upserted_economic_indices=11,
    )
    assert len(repository.refresh_calls) == 5
    assert sum(len(call.exchange_rates) for call in repository.refresh_calls) == 1106
    assert sum(len(call.economic_indices) for call in repository.refresh_calls) == 11


@pytest.mark.asyncio
async def test_sync_recent_market_data_skips_persisting_codes_with_no_results() -> None:
    """Test syncing skips repository writes when a requested code returns no entries."""
    repository = StubMarketDataRepository(
        existing_exchange_rate_dates={
            "USD": {date(2026, 5, 8)},
            "EUR": {date(2026, 5, 8)},
            "UF": {date(2026, 5, 8)},
        },
        existing_index_periods={"IPC_CL": {(2026, 5)}},
    )
    use_case = SyncRecentMarketData(
        repository,
        StubFxProvider(
            missing_requests={
                ("UTM", requested_date)
                for requested_date in (
                    date(2025, 6, 1),
                    date(2025, 7, 1),
                    date(2025, 8, 1),
                    date(2025, 9, 1),
                    date(2025, 10, 1),
                    date(2025, 11, 1),
                    date(2025, 12, 1),
                    date(2026, 1, 1),
                    date(2026, 2, 1),
                    date(2026, 3, 1),
                    date(2026, 4, 1),
                    date(2026, 5, 1),
                )
            }
        ),
        StubEconomicIndexProvider(
            missing_requests={
                ("IPC_CL", period_year, period_month)
                for period_year, period_month in (
                    (2025, 6),
                    (2025, 7),
                    (2025, 8),
                    (2025, 9),
                    (2025, 10),
                    (2025, 11),
                    (2025, 12),
                    (2026, 1),
                    (2026, 2),
                    (2026, 3),
                    (2026, 4),
                )
            }
        ),
        today_provider=lambda: date(2026, 5, 8),
    )

    result = await use_case.execute()

    assert result == SyncRecentMarketDataResultDTO(
        requested_exchange_rates=1104,
        requested_economic_indices=11,
        upserted_exchange_rates=1092,
        upserted_economic_indices=0,
    )
    assert len(repository.refresh_calls) == 3


@pytest.mark.asyncio
async def test_sync_recent_market_data_clears_remaining_request_after_success() -> None:
    """Test explicit sync clears the requested gaps when providers return data."""
    repository = StubMarketDataRepository()
    use_case = SyncRecentMarketData(
        repository,
        StubFxProvider(),
        StubEconomicIndexProvider(),
        today_provider=lambda: date(2026, 5, 8),
    )

    result, remaining_request = await use_case.execute_request_and_collect_remaining(
        request=_APRIL_2026_MARKET_DATA_REQUEST
    )

    assert result == SyncRecentMarketDataResultDTO(
        requested_exchange_rates=2,
        requested_economic_indices=1,
        upserted_exchange_rates=2,
        upserted_economic_indices=1,
    )
    assert remaining_request is None


@pytest.mark.asyncio
async def test_sync_recent_market_data_keeps_remaining_request_on_provider_miss() -> (
    None
):
    """Test explicit sync returns any still-missing requested entries."""
    repository = StubMarketDataRepository()
    use_case = SyncRecentMarketData(
        repository,
        StubFxProvider(missing_requests={("UF", date(2026, 4, 29))}),
        StubEconomicIndexProvider(missing_requests={("IPC_CL", 2026, 4)}),
        today_provider=lambda: date(2026, 5, 8),
    )

    result, remaining_request = await use_case.execute_request_and_collect_remaining(
        request=_APRIL_2026_MARKET_DATA_REQUEST
    )

    assert result == SyncRecentMarketDataResultDTO(
        requested_exchange_rates=2,
        requested_economic_indices=1,
        upserted_exchange_rates=1,
        upserted_economic_indices=0,
    )
    assert remaining_request == MarketDataSyncRequestDTO(
        exchange_rate_dates={"UF": [date(2026, 4, 29)]},
        economic_index_periods={"IPC_CL": [(2026, 4)]},
    )


@pytest.mark.asyncio
async def test_sync_recent_market_data_ignores_empty_requested_groups() -> None:
    """Test explicit sync skips empty request groups when checking remainders."""
    repository = StubMarketDataRepository()
    use_case = SyncRecentMarketData(
        repository,
        StubFxProvider(),
        StubEconomicIndexProvider(),
        today_provider=lambda: date(2026, 5, 8),
    )

    remaining_request = await use_case.collect_remaining_request(
        MarketDataSyncRequestDTO(
            exchange_rate_dates={"UF": []},
            economic_index_periods={"IPC_CL": []},
        )
    )

    assert remaining_request is None
