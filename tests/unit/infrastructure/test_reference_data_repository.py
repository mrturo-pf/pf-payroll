from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.domain.contributions import HealthInstitutionKind
from payroll.infrastructure.db.models import (
    ContributionCapModel,
    CurrencyModel,
    HealthInstitutionModel,
    HealthPlanModel,
    IncomeTaxBracketModel,
    PayrollConceptModel,
    PensionInstitutionModel,
    PensionPlanModel,
)
from payroll.infrastructure.db.models.reference_data import ContributionCapType, PayrollConceptKind
from payroll.infrastructure.db.repositories.reference_data_repository import SqlAlchemyReferenceDataRepository
from payroll.interfaces.api import dependencies


class FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class FakeResult:
    def __init__(self, scalar_rows: list[object] | None = None, joined_rows: list[tuple[object, object]] | None = None) -> None:
        self._scalar_rows = scalar_rows or []
        self._joined_rows = joined_rows or []

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self._scalar_rows)

    def all(self) -> list[tuple[object, object]]:
        return self._joined_rows


class FakeSession:
    def __init__(self, results: list[FakeResult]) -> None:
        self._results = results
        self.statements: list[object] = []

    async def execute(self, statement: object) -> FakeResult:
        self.statements.append(statement)
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_sqlalchemy_reference_data_repository_maps_all_catalogs() -> None:
    session = FakeSession(
        [
            FakeResult(scalar_rows=[SimpleNamespace(code="CLP", name="Peso chileno", is_fiat=True, unit_kind="currency")]),
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(code="AFP_UNO", name="AFP Uno", mandatory_rate=Decimal("0.10"), is_active=True)
                ]
            ),
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(
                        code="FONASA",
                        name="Fonasa",
                        kind=HealthInstitutionKind.FONASA,
                        mandatory_rate=Decimal("0.07"),
                        is_active=True,
                    )
                ]
            ),
            FakeResult(
                joined_rows=[
                    (
                        SimpleNamespace(
                            id=1,
                            valid_from=date(2024, 1, 1),
                            valid_to=None,
                            additional_rate=Decimal("0"),
                        ),
                        SimpleNamespace(code="AFP_UNO", name="AFP Uno"),
                    )
                ]
            ),
            FakeResult(
                joined_rows=[
                    (
                        SimpleNamespace(
                            id=2,
                            valid_from=date(2024, 1, 1),
                            valid_to=None,
                            plan_name="Base",
                            contracted_uf=Decimal("0"),
                        ),
                        SimpleNamespace(
                            code="FONASA",
                            name="Fonasa",
                            kind=HealthInstitutionKind.FONASA,
                        ),
                    )
                ]
            ),
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(
                        cap_type=SimpleNamespace(value="pension_health"),
                        valid_from=date(2026, 1, 1),
                        valid_to=None,
                        value_uf=Decimal("90.0600"),
                    )
                ]
            ),
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(code="SALARY_BASE", name="Base Salary", kind=SimpleNamespace(value="income"), is_taxable=True)
                ]
            ),
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(
                        valid_from=date(2026, 1, 1),
                        valid_to=None,
                        lower_bound_utm=Decimal("0"),
                        upper_bound_utm=Decimal("13.5"),
                        marginal_rate=Decimal("0"),
                        rebate_utm=Decimal("0"),
                    )
                ]
            ),
        ]
    )
    repository = SqlAlchemyReferenceDataRepository(session)

    assert [item.code for item in await repository.list_currencies()] == ["CLP"]
    assert [item.code for item in await repository.list_pension_institutions()] == ["AFP_UNO"]
    assert [item.code for item in await repository.list_health_institutions()] == ["FONASA"]
    assert [item.id for item in await repository.list_pension_plans()] == [1]
    assert [item.id for item in await repository.list_health_plans()] == [2]
    assert [item.cap_type for item in await repository.list_contribution_caps()] == ["pension_health"]
    assert [item.code for item in await repository.list_payroll_concepts()] == ["SALARY_BASE"]
    assert [item.lower_bound_utm for item in await repository.list_income_tax_brackets()] == [Decimal("0")]
    assert len(session.statements) == 8


@pytest.mark.asyncio
async def test_api_dependencies_build_repository_queries_and_session(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = object()
    exit_called = False

    class FakeSessionManager:
        async def __aenter__(self) -> object:
            return fake_session

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            nonlocal exit_called
            exit_called = True

    monkeypatch.setattr(dependencies, "SessionLocal", lambda: FakeSessionManager())

    iterator: AsyncIterator[object] = dependencies.get_session()
    yielded = await anext(iterator)
    assert yielded is fake_session
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    assert exit_called is True

    repository = dependencies.get_reference_data_repository(fake_session)  # type: ignore[arg-type]
    queries = dependencies.get_reference_data_queries(repository)

    assert isinstance(repository, SqlAlchemyReferenceDataRepository)
    assert isinstance(queries, ReferenceDataQueries)


def test_reference_data_models_and_enums_are_declared() -> None:
    assert CurrencyModel.__tablename__ == "currencies"
    assert PensionInstitutionModel.__tablename__ == "pension_institutions"
    assert HealthInstitutionModel.__tablename__ == "health_institutions"
    assert PensionPlanModel.__tablename__ == "pension_plans"
    assert HealthPlanModel.__tablename__ == "health_plans"
    assert ContributionCapModel.__tablename__ == "contribution_caps"
    assert IncomeTaxBracketModel.__tablename__ == "income_tax_brackets"
    assert PayrollConceptModel.__tablename__ == "payroll_concepts"
    assert ContributionCapType.PENSION_HEALTH.value == "pension_health"
    assert PayrollConceptKind.INCOME.value == "income"
