"""Market-data routes."""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from payroll.application.dto import (
    EconomicIndexWriteDTO,
    ExchangeRateWriteDTO,
    ProviderEconomicIndexRequestDTO,
    ProviderExchangeRateRequestDTO,
    RefreshRatesCommandDTO,
)
from payroll.application.use_cases.market_data import MarketDataQueries
from payroll.application.use_cases.refresh_rates import RefreshRates
from payroll.interfaces.api.dependencies import get_market_data_queries, get_refresh_rates_use_case

router = APIRouter(prefix="/market-data", tags=["market-data"])


class ExchangeRateRead(BaseModel):
    currency_code: str
    rate_date: date
    value_clp: str
    source: str


class EconomicIndexRead(BaseModel):
    code: str
    period_year: int
    period_month: int
    index_value: str
    monthly_change: str | None
    yearly_change: str | None
    base_period: str
    source: str


class ExchangeRateWrite(BaseModel):
    currency_code: str = Field(min_length=1)
    rate_date: date
    value_clp: Decimal = Field(gt=0)
    source: str = Field(default="manual", min_length=1)


class EconomicIndexWrite(BaseModel):
    code: str = Field(min_length=1)
    period_year: int = Field(ge=1990, le=2100)
    period_month: int = Field(ge=1, le=12)
    index_value: Decimal = Field(gt=0)
    monthly_change: Decimal | None = None
    yearly_change: Decimal | None = None
    base_period: str = Field(default="DIC-2018", min_length=1)
    source: str = Field(default="manual", min_length=1)


class ProviderExchangeRateRequest(BaseModel):
    currency_code: str = Field(min_length=1)
    rate_date: date


class ProviderEconomicIndexRequest(BaseModel):
    code: str = Field(min_length=1)
    period_year: int = Field(ge=1990, le=2100)
    period_month: int = Field(ge=1, le=12)


class RefreshRatesRequest(BaseModel):
    exchange_rates: list[ExchangeRateWrite] = Field(default_factory=list)
    economic_indices: list[EconomicIndexWrite] = Field(default_factory=list)
    fetch_exchange_rates: list[ProviderExchangeRateRequest] = Field(default_factory=list)
    fetch_economic_indices: list[ProviderEconomicIndexRequest] = Field(default_factory=list)


class RefreshRatesResponse(BaseModel):
    upserted_exchange_rates: int
    upserted_economic_indices: int


@router.get("/exchange-rates", response_model=list[ExchangeRateRead])
async def list_exchange_rates(
    currency_code: str | None = Query(default=None),
    queries: MarketDataQueries = Depends(get_market_data_queries),
) -> list[ExchangeRateRead]:
    return [
        ExchangeRateRead(
            currency_code=item.currency_code,
            rate_date=item.rate_date,
            value_clp=str(item.value_clp),
            source=item.source,
        )
        for item in await queries.list_exchange_rates(currency_code)
    ]


@router.get("/economic-indices", response_model=list[EconomicIndexRead])
async def list_economic_indices(
    code: str | None = Query(default=None),
    queries: MarketDataQueries = Depends(get_market_data_queries),
) -> list[EconomicIndexRead]:
    return [
        EconomicIndexRead(
            code=item.code,
            period_year=item.period_year,
            period_month=item.period_month,
            index_value=str(item.index_value),
            monthly_change=str(item.monthly_change) if item.monthly_change is not None else None,
            yearly_change=str(item.yearly_change) if item.yearly_change is not None else None,
            base_period=item.base_period,
            source=item.source,
        )
        for item in await queries.list_economic_indices(code)
    ]


@router.post("/refresh", response_model=RefreshRatesResponse)
async def refresh_rates(
    payload: RefreshRatesRequest,
    use_case: RefreshRates = Depends(get_refresh_rates_use_case),
) -> RefreshRatesResponse:
    try:
        result = await use_case.execute(
            RefreshRatesCommandDTO(
                exchange_rates=[
                    ExchangeRateWriteDTO(
                        currency_code=item.currency_code,
                        rate_date=item.rate_date,
                        value_clp=item.value_clp,
                        source=item.source,
                    )
                    for item in getattr(payload, "exchange_rates", [])
                ],
                economic_indices=[
                    EconomicIndexWriteDTO(
                        code=item.code,
                        period_year=item.period_year,
                        period_month=item.period_month,
                        index_value=item.index_value,
                        monthly_change=item.monthly_change,
                        yearly_change=item.yearly_change,
                        base_period=item.base_period,
                        source=item.source,
                    )
                    for item in getattr(payload, "economic_indices", [])
                ],
                provider_exchange_rates=[
                    ProviderExchangeRateRequestDTO(currency_code=item.currency_code, rate_date=item.rate_date)
                    for item in getattr(payload, "fetch_exchange_rates", [])
                ],
                provider_economic_indices=[
                    ProviderEconomicIndexRequestDTO(
                        code=item.code,
                        period_year=item.period_year,
                        period_month=item.period_month,
                    )
                    for item in getattr(payload, "fetch_economic_indices", [])
                ],
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RefreshRatesResponse(
        upserted_exchange_rates=result.upserted_exchange_rates,
        upserted_economic_indices=result.upserted_economic_indices,
    )
