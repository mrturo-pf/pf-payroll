from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from payroll.application.dto import EconomicIndexDTO, ExchangeRateDTO, RefreshRatesResultDTO
from payroll.interfaces.api.dependencies import get_market_data_queries, get_refresh_rates_use_case
from payroll.interfaces.api.main import app
from payroll.interfaces.api.routes.market_data import refresh_rates


class FakeMarketDataQueries:
    async def list_exchange_rates(self, currency_code: str | None = None) -> list[ExchangeRateDTO]:
        assert currency_code in (None, "UF")
        return [ExchangeRateDTO(currency_code="UF", rate_date=date(2026, 1, 31), value_clp=Decimal("38000"), source="manual")]

    async def list_economic_indices(self, code: str | None = None) -> list[EconomicIndexDTO]:
        assert code in (None, "IPC_CL")
        return [
            EconomicIndexDTO(
                code="IPC_CL",
                period_year=2026,
                period_month=1,
                index_value=Decimal("112.340000"),
                monthly_change=Decimal("0.7000"),
                yearly_change=Decimal("4.1000"),
                base_period="DIC-2018",
                source="manual",
            )
        ]


class FakeRefreshRates:
    async def execute(self, command: object) -> RefreshRatesResultDTO:
        assert len(getattr(command, "exchange_rates")) == 1
        assert len(getattr(command, "economic_indices")) == 1
        assert len(getattr(command, "provider_exchange_rates")) == 0
        assert len(getattr(command, "provider_economic_indices")) == 0
        return RefreshRatesResultDTO(upserted_exchange_rates=1, upserted_economic_indices=1)


class FakeProviderRefreshRates:
    async def execute(self, command: object) -> RefreshRatesResultDTO:
        assert len(getattr(command, "exchange_rates")) == 0
        assert len(getattr(command, "economic_indices")) == 0
        assert len(getattr(command, "provider_exchange_rates")) == 1
        assert len(getattr(command, "provider_economic_indices")) == 1
        return RefreshRatesResultDTO(upserted_exchange_rates=1, upserted_economic_indices=1)


def test_market_data_endpoints() -> None:
    app.dependency_overrides[get_market_data_queries] = lambda: FakeMarketDataQueries()
    app.dependency_overrides[get_refresh_rates_use_case] = lambda: FakeRefreshRates()
    client = TestClient(app)

    try:
        exchange_rates = client.get("/market-data/exchange-rates", params={"currency_code": "UF"})
        economic_indices = client.get("/market-data/economic-indices", params={"code": "IPC_CL"})
        refresh_response = client.post(
            "/market-data/refresh",
            json={
                "exchange_rates": [
                    {
                        "currency_code": "UF",
                        "rate_date": "2026-01-31",
                        "value_clp": "38000",
                        "source": "manual",
                    }
                ],
                "economic_indices": [
                    {
                        "code": "IPC_CL",
                        "period_year": 2026,
                        "period_month": 1,
                        "index_value": "112.340000",
                        "monthly_change": "0.7000",
                        "yearly_change": "4.1000",
                        "base_period": "DIC-2018",
                        "source": "manual",
                    }
                ],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert exchange_rates.status_code == 200
    assert exchange_rates.json() == [
        {
            "currency_code": "UF",
            "rate_date": "2026-01-31",
            "value_clp": "38000",
            "source": "manual",
        }
    ]
    assert economic_indices.status_code == 200
    assert economic_indices.json() == [
        {
            "code": "IPC_CL",
            "period_year": 2026,
            "period_month": 1,
            "index_value": "112.340000",
            "monthly_change": "0.7000",
            "yearly_change": "4.1000",
            "base_period": "DIC-2018",
            "source": "manual",
        }
    ]
    assert refresh_response.status_code == 200
    assert refresh_response.json() == {"upserted_exchange_rates": 1, "upserted_economic_indices": 1}


def test_market_data_refresh_endpoint_surfaces_domain_errors() -> None:
    class ErrorRefreshRates:
        async def execute(self, command: object) -> RefreshRatesResultDTO:
            raise ValueError("bad market data payload")

    app.dependency_overrides[get_refresh_rates_use_case] = lambda: ErrorRefreshRates()
    client = TestClient(app)

    try:
        response = client.post("/market-data/refresh", json={"exchange_rates": [], "economic_indices": []})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json() == {"detail": "bad market data payload"}


def test_market_data_refresh_endpoint_accepts_provider_fetch_requests() -> None:
    app.dependency_overrides[get_refresh_rates_use_case] = lambda: FakeProviderRefreshRates()
    client = TestClient(app)

    try:
        response = client.post(
            "/market-data/refresh",
            json={
                "fetch_exchange_rates": [{"currency_code": "UF", "rate_date": "2026-01-31"}],
                "fetch_economic_indices": [{"code": "IPC_CL", "period_year": 2026, "period_month": 4}],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"upserted_exchange_rates": 1, "upserted_economic_indices": 1}


@pytest.mark.asyncio
async def test_market_data_refresh_handler_maps_value_errors() -> None:
    class ErrorRefreshRates:
        async def execute(self, command: object) -> RefreshRatesResultDTO:
            raise ValueError("invalid refresh")

    with pytest.raises(HTTPException, match="invalid refresh"):
        await refresh_rates(
            payload=SimpleNamespace(exchange_rates=[], economic_indices=[]),
            use_case=ErrorRefreshRates(),
        )
