"""Tests for test reference data repository."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from helpers.db_fakes import (
    FakeAllMixin,
    FakeResultsQueueBase,
    FakeScalarResult,
    assert_get_session_lifecycle,
)
from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.domain.contributions import HealthInstitutionKind
from payroll.infrastructure.db.models import (
    ContributionCapModel,
    HealthInstitutionModel,
    HealthPlanModel,
    PayrollConceptModel,
    PensionInstitutionModel,
    PensionPlanModel,
)
from payroll.infrastructure.db.models.reference_data import (
    ContributionCapType,
    PayrollConceptKind,
)
from payroll.infrastructure.db.repositories.reference_data_repository import (
    SqlAlchemyReferenceDataRepository,
)
from payroll.interfaces.api import dependencies


class FakeResult(FakeAllMixin):
    """Test double for Result."""

    def __init__(
        self,
        scalar_rows: list[object] | None = None,
        joined_rows: list[tuple[object, object]] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._scalar_rows = scalar_rows or []
        self._joined_rows = joined_rows or []

    def scalars(self) -> FakeScalarResult:
        """Handle scalars."""
        return FakeScalarResult(self._scalar_rows)


class FakeSession(FakeResultsQueueBase):
    """Test double for Session."""

    def __init__(self, results: list[FakeResult]) -> None:
        """Initialize the instance."""
        super().__init__(results)  # type: ignore[arg-type]
        self.statements: list[object] = []
        self.commit_calls = 0

    async def execute(self, statement: object) -> FakeResult:
        """Handle execute."""
        self.statements.append(statement)
        return self._results.pop(0)

    async def commit(self) -> None:
        """Handle commit."""
        self.commit_calls += 1


@pytest.mark.asyncio
async def test_sqlalchemy_reference_data_repository_maps_all_catalogs() -> None:
    """Test sqlalchemy reference data repository maps all catalogs."""
    session = FakeSession(
        [
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(
                        code="AFP_UNO",
                        name="AFP Uno",
                        mandatory_rate=Decimal("0.10"),
                        is_active=True,
                    )
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
                    SimpleNamespace(
                        code="SALARY_BASE",
                        name="Base Salary",
                        kind=SimpleNamespace(value="income"),
                        is_taxable=True,
                    )
                ]
            ),
        ]
    )
    repository = SqlAlchemyReferenceDataRepository(session)

    assert [item.code for item in await repository.list_pension_institutions()] == [
        "AFP_UNO"
    ]
    assert [item.code for item in await repository.list_health_institutions()] == [
        "FONASA"
    ]
    assert [item.id for item in await repository.list_pension_plans()] == [1]
    assert [item.id for item in await repository.list_health_plans()] == [2]
    assert [item.cap_type for item in await repository.list_contribution_caps()] == [
        "pension_health"
    ]
    assert [item.code for item in await repository.list_payroll_concepts()] == [
        "SALARY_BASE"
    ]
    assert len(session.statements) == 6


@pytest.mark.asyncio
async def test_reference_data_repository_can_include_inactive_health_catalogs() -> None:
    """Test health catalogs can include inactive institutions and plans."""
    inactive_institution = SimpleNamespace(
        code="LEGACY",
        name="Legacy",
        kind=HealthInstitutionKind.ISAPRE,
        mandatory_rate=Decimal("0.07"),
        is_active=False,
    )
    inactive_plan = (
        SimpleNamespace(
            id=9,
            valid_from=date(2024, 1, 1),
            valid_to=None,
            plan_name="Closed",
            contracted_uf=Decimal("1.5"),
        ),
        SimpleNamespace(
            code="LEGACY",
            name="Legacy",
            kind=HealthInstitutionKind.ISAPRE,
        ),
    )
    session = FakeSession(
        [
            FakeResult(scalar_rows=[inactive_institution]),
            FakeResult(joined_rows=[inactive_plan]),
        ]
    )
    repository = SqlAlchemyReferenceDataRepository(session)

    health_institutions = await repository.list_health_institutions(
        include_inactive=True
    )
    health_plans = await repository.list_health_plans(include_inactive=True)

    assert [item.code for item in health_institutions] == ["LEGACY"]
    assert [item.id for item in health_plans] == [9]


@pytest.mark.asyncio
async def test_api_dependencies_build_repository_queries_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test api dependencies build repository queries and session."""
    fake_session = await assert_get_session_lifecycle(monkeypatch, dependencies)

    repository = dependencies.get_reference_data_repository(fake_session)  # type: ignore[arg-type]
    queries = dependencies.get_reference_data_queries(repository)

    assert isinstance(repository, SqlAlchemyReferenceDataRepository)
    assert isinstance(queries, ReferenceDataQueries)


@pytest.mark.asyncio
async def test_reference_data_repo_returns_none_when_no_pension_plan_for_date() -> None:
    """Test returning None when no pension plan matches the reference date."""
    session = FakeSession([FakeResult(joined_rows=[])])
    repository = SqlAlchemyReferenceDataRepository(session)

    result = await repository.get_valid_pension_plan_for_date(date(2026, 1, 1))

    assert result is None


def test_reference_data_models_and_enums_are_declared() -> None:
    """Test reference data models and enums are declared."""
    assert PensionInstitutionModel.__tablename__ == "pension_institutions"
    assert HealthInstitutionModel.__tablename__ == "health_institutions"
    assert PensionPlanModel.__tablename__ == "pension_plans"
    assert HealthPlanModel.__tablename__ == "health_plans"
    assert ContributionCapModel.__tablename__ == "contribution_caps"
    assert PayrollConceptModel.__tablename__ == "payroll_concepts"
    assert ContributionCapType.PENSION_HEALTH.value == "pension_health"
    assert PayrollConceptKind.INCOME.value == "income"
