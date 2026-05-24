"""SQLAlchemy repository for exchange rates and economic indices."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.errors import PayrollValidationError
from payroll.application.dto import (
    EconomicIndexDTO,
    ExchangeRateDTO,
    RefreshRatesCommandDTO,
    RefreshRatesResultDTO,
)
from payroll.infrastructure.db.models.reference_data import (
    CurrencyModel,
    EconomicIndexModel,
    ExchangeRateModel,
)


class SqlAlchemyMarketDataRepository:
    """Provide sql alchemy market data repository."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the instance."""
        self._session = session

    async def list_exchange_rates(
        self, currency_code: str | None = None
    ) -> list[ExchangeRateDTO]:
        """List exchange rates."""
        statement = select(ExchangeRateModel).order_by(
            ExchangeRateModel.rate_date.desc(), ExchangeRateModel.currency_code
        )
        if currency_code is not None:
            statement = statement.where(
                ExchangeRateModel.currency_code == currency_code
            )

        result = await self._session.execute(statement)
        return [
            ExchangeRateDTO(
                currency_code=row.currency_code.strip(),
                rate_date=row.rate_date,
                value_clp=row.value_clp,
                source=row.source,
            )
            for row in result.scalars().all()
        ]

    async def list_economic_indices(
        self, code: str | None = None
    ) -> list[EconomicIndexDTO]:
        """List economic indices."""
        statement = select(EconomicIndexModel).order_by(
            EconomicIndexModel.code,
            EconomicIndexModel.period_year.desc(),
            EconomicIndexModel.period_month.desc(),
        )
        if code is not None:
            statement = statement.where(EconomicIndexModel.code == code)

        result = await self._session.execute(statement)
        return [
            EconomicIndexDTO(
                code=row.code,
                period_year=row.period_year,
                period_month=row.period_month,
                index_value=row.index_value,
                monthly_change=row.monthly_change,
                yearly_change=row.yearly_change,
                base_period=row.base_period,
                source=row.source,
            )
            for row in result.scalars().all()
        ]

    async def get_exchange_rate_value(
        self, currency_code: str, rate_date: date
    ) -> Decimal | None:
        """Get exchange rate value."""
        result = await self._session.execute(
            select(ExchangeRateModel.value_clp).where(
                ExchangeRateModel.currency_code == currency_code,
                ExchangeRateModel.rate_date == rate_date,
            )
        )
        return result.scalar_one_or_none()

    async def list_exchange_rate_dates(
        self, currency_code: str, start_date: date, end_date: date
    ) -> list[date]:
        """List exchange-rate dates."""
        result = await self._session.execute(
            select(ExchangeRateModel.rate_date).where(
                ExchangeRateModel.currency_code == currency_code,
                ExchangeRateModel.rate_date >= start_date,
                ExchangeRateModel.rate_date <= end_date,
            )
        )
        return list(result.scalars().all())

    async def get_economic_index_value(
        self, code: str, period_year: int, period_month: int
    ) -> Decimal | None:
        """Get economic index value."""
        result = await self._session.execute(
            select(EconomicIndexModel.index_value).where(
                EconomicIndexModel.code == code,
                EconomicIndexModel.period_year == period_year,
                EconomicIndexModel.period_month == period_month,
            )
        )
        return result.scalar_one_or_none()

    async def list_economic_index_periods(
        self,
        code: str,
        start_year: int,
        start_month: int,
        end_year: int,
        end_month: int,
    ) -> list[tuple[int, int]]:
        """List economic-index periods."""
        start_key = start_year * 100 + start_month
        end_key = end_year * 100 + end_month
        period_key = (
            EconomicIndexModel.period_year * 100 + EconomicIndexModel.period_month
        )
        result = await self._session.execute(
            select(EconomicIndexModel).where(
                EconomicIndexModel.code == code,
                period_key >= start_key,
                period_key <= end_key,
            )
        )
        return [(row.period_year, row.period_month) for row in result.scalars().all()]

    async def refresh_rates(
        self, command: RefreshRatesCommandDTO
    ) -> RefreshRatesResultDTO:
        """Refresh rates."""
        if command.exchange_rates:
            currency_result = await self._session.execute(
                select(CurrencyModel.code).where(
                    CurrencyModel.code.in_(
                        {entry.currency_code for entry in command.exchange_rates}
                    )
                )
            )
            known_currencies = {
                code.strip() for code in currency_result.scalars().all()
            }
            missing_currencies = sorted(
                {entry.currency_code for entry in command.exchange_rates}
                - known_currencies
            )
            if missing_currencies:
                raise PayrollValidationError(
                    "Unknown currencies in exchange rates: "
                    f"{', '.join(missing_currencies)}"
                )

            exchange_rate_insert = insert(ExchangeRateModel)
            await self._session.execute(
                exchange_rate_insert.values(
                    [
                        {
                            "currency_code": entry.currency_code,
                            "rate_date": entry.rate_date,
                            "value_clp": entry.value_clp,
                            "source": entry.source,
                        }
                        for entry in command.exchange_rates
                    ]
                ).on_conflict_do_update(
                    index_elements=[
                        ExchangeRateModel.currency_code,
                        ExchangeRateModel.rate_date,
                    ],
                    set_={
                        "value_clp": exchange_rate_insert.excluded.value_clp,
                        "source": exchange_rate_insert.excluded.source,
                    },
                )
            )

        if command.economic_indices:
            economic_index_insert = insert(EconomicIndexModel)
            await self._session.execute(
                economic_index_insert.values(
                    [
                        {
                            "code": entry.code,
                            "period_year": entry.period_year,
                            "period_month": entry.period_month,
                            "index_value": entry.index_value,
                            "monthly_change": entry.monthly_change,
                            "yearly_change": entry.yearly_change,
                            "base_period": entry.base_period,
                            "source": entry.source,
                        }
                        for entry in command.economic_indices
                    ]
                ).on_conflict_do_update(
                    index_elements=[
                        EconomicIndexModel.code,
                        EconomicIndexModel.period_year,
                        EconomicIndexModel.period_month,
                    ],
                    set_={
                        "index_value": economic_index_insert.excluded.index_value,
                        "monthly_change": economic_index_insert.excluded.monthly_change,
                        "yearly_change": economic_index_insert.excluded.yearly_change,
                        "base_period": economic_index_insert.excluded.base_period,
                        "source": economic_index_insert.excluded.source,
                    },
                )
            )

        await self._session.commit()
        return RefreshRatesResultDTO(
            upserted_exchange_rates=len(command.exchange_rates),
            upserted_economic_indices=len(command.economic_indices),
        )
