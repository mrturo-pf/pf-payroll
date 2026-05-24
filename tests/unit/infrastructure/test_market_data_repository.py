"""Tests for test market data repository."""

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from payroll.application.use_cases.market_data import MarketDataQueries
from payroll.application.use_cases.deflate_amounts import DeflateAmounts
from payroll.application.use_cases.refresh_rates import RefreshRates
from payroll.infrastructure.db.models import CurrencyModel, EconomicIndexModel, ExchangeRateModel
from payroll.infrastructure.db.repositories.market_data_repository import SqlAlchemyMarketDataRepository
from payroll.interfaces.api import dependencies


class FakeScalarResult:
    """Test double for Scalar Result."""

    def __init__(self, rows: list[object]) -> None:
        """Initialize the instance."""
        self._rows = rows

    def all(self) -> list[object]:
        """Handle all."""
        return self._rows


class FakeResult:
    """Test double for Result."""

    def __init__(self, scalar_rows: list[object] | None = None, scalar_one: object | None = None) -> None:
        """Initialize the instance."""
        self._scalar_rows = scalar_rows or []
        self._scalar_one = scalar_one

    def scalars(self) -> FakeScalarResult:
        """Handle scalars."""
        return FakeScalarResult(self._scalar_rows)

    def scalar_one_or_none(self) -> object | None:
        """Handle scalar one or none."""
        return self._scalar_one


class FakeSession:
    """Test double for Session."""

    def __init__(self, results: list[FakeResult]) -> None:
        """Initialize the instance."""
        self._results = results
        self.statements: list[object] = []
        self.commit_count = 0

    async def execute(self, statement: object) -> FakeResult:
        """Handle execute."""
        self.statements.append(statement)
        if self._results:
            return self._results.pop(0)
        return FakeResult()

    async def commit(self) -> None:
        """Handle commit."""
        self.commit_count += 1


@pytest.mark.asyncio
async def test_sqlalchemy_market_data_repository_lists_rates_and_indices() -> None:
    """Test sqlalchemy market data repository lists rates and indices."""
    session = FakeSession(
        [
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(currency_code="UF", rate_date=date(2026, 1, 31), value_clp=Decimal("38000"), source="manual")
                ]
            ),
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(
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
            ),
            FakeResult(scalar_one=Decimal("38000")),
            FakeResult(scalar_one=Decimal("38000")),
        ]
    )
    repository = SqlAlchemyMarketDataRepository(session)  # type: ignore[arg-type]

    exchange_rates = await repository.list_exchange_rates("UF")
    economic_indices = await repository.list_economic_indices("IPC_CL")
    uf_value = await repository.get_exchange_rate_value("UF", date(2026, 1, 31))
    ipc_value = await repository.get_economic_index_value("IPC_CL", 2026, 1)

    assert [item.currency_code for item in exchange_rates] == ["UF"]
    assert [item.code for item in economic_indices] == ["IPC_CL"]
    assert uf_value == Decimal("38000")
    assert ipc_value == Decimal("38000")
    assert len(session.statements) == 4


@pytest.mark.asyncio
async def test_sqlalchemy_market_data_repository_refreshes_entries_and_validates_currencies() -> None:
    """Test sqlalchemy market data repository refreshes entries and validates currencies."""
    session = FakeSession([FakeResult(scalar_rows=["UF ", "USD"]), FakeResult(), FakeResult()])
    repository = SqlAlchemyMarketDataRepository(session)  # type: ignore[arg-type]

    result = await repository.refresh_rates(
        SimpleNamespace(
            exchange_rates=[
                SimpleNamespace(currency_code="UF", rate_date=date(2026, 1, 31), value_clp=Decimal("38000"), source="manual"),
                SimpleNamespace(currency_code="USD", rate_date=date(2026, 1, 31), value_clp=Decimal("980.5"), source="manual"),
            ],
            economic_indices=[
                SimpleNamespace(
                    code="IPC_CL",
                    period_year=2026,
                    period_month=1,
                    index_value=Decimal("112.340000"),
                    monthly_change=Decimal("0.7000"),
                    yearly_change=Decimal("4.1000"),
                    base_period="DIC-2018",
                    source="manual",
                )
            ],
        )
    )

    assert result.upserted_exchange_rates == 2
    assert result.upserted_economic_indices == 1
    assert session.commit_count == 1
    assert len(session.statements) == 3


@pytest.mark.asyncio
async def test_sqlalchemy_market_data_repository_rejects_unknown_currencies() -> None:
    """Test sqlalchemy market data repository rejects unknown currencies."""
    repository = SqlAlchemyMarketDataRepository(FakeSession([FakeResult(scalar_rows=["UF"])]))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Unknown currencies in exchange rates: UTM"):
        await repository.refresh_rates(
            SimpleNamespace(
                exchange_rates=[
                    SimpleNamespace(currency_code="UF", rate_date=date(2026, 1, 31), value_clp=Decimal("38000"), source="manual"),
                    SimpleNamespace(currency_code="UTM", rate_date=date(2026, 1, 31), value_clp=Decimal("67000"), source="manual"),
                ],
                economic_indices=[],
            )
        )


@pytest.mark.asyncio
async def test_api_dependencies_build_market_data_repository_queries_use_case_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test api dependencies build market data repository queries use case and session."""
    fake_session = object()
    exited = False

    class FakeSessionManager:
        """Test double for Session Manager."""

        async def __aenter__(self) -> object:
            """Enter the async context manager."""
            return fake_session

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            """Exit the async context manager."""
            nonlocal exited
            exited = True

    monkeypatch.setattr(dependencies, "SessionLocal", lambda: FakeSessionManager())

    iterator: AsyncIterator[object] = dependencies.get_session()
    assert await anext(iterator) is fake_session
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    assert exited is True

    repository = dependencies.get_market_data_repository(fake_session)  # type: ignore[arg-type]
    queries = dependencies.get_market_data_queries(repository)
    use_case = dependencies.get_refresh_rates_use_case(repository)
    deflate_use_case = dependencies.get_deflate_amounts_use_case(object(), repository)  # type: ignore[arg-type]

    assert isinstance(repository, SqlAlchemyMarketDataRepository)
    assert isinstance(queries, MarketDataQueries)
    assert isinstance(use_case, RefreshRates)
    assert isinstance(deflate_use_case, DeflateAmounts)


def test_market_data_models_are_declared() -> None:
    """Test market data models are declared."""
    assert CurrencyModel.__tablename__ == "currencies"
    assert ExchangeRateModel.__tablename__ == "exchange_rates"
    assert EconomicIndexModel.__tablename__ == "economic_indices"
