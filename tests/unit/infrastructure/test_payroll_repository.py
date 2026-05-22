from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.domain.contributions import HealthContribution, HealthInstitutionKind, PensionContribution
from payroll.infrastructure.db.models import EmployerModel, PayrollItemModel, PayrollPeriodModel
from payroll.infrastructure.db.models.payroll import PayrollStatus
from payroll.infrastructure.db.models.reference_data import (
    ContributionCapModel,
    ContributionCapType,
    HealthInstitutionModel,
    HealthPlanModel,
    PensionInstitutionModel,
    PensionPlanModel,
)
from payroll.infrastructure.db.repositories.payroll_repository import SqlAlchemyPayrollRepository
from payroll.interfaces.api import dependencies


class FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class FakeResult:
    def __init__(
        self,
        scalar_rows: list[object] | None = None,
        scalar_one: object | None = None,
        first_row: object | None = None,
    ) -> None:
        self._scalar_rows = scalar_rows or []
        self._scalar_one = scalar_one
        self._first_row = first_row

    def scalars(self) -> FakeScalarResult:
        return FakeScalarResult(self._scalar_rows)

    def scalar_one_or_none(self) -> object | None:
        return self._scalar_one

    def scalar_one(self) -> object:
        return self._scalar_one

    def first(self) -> object | None:
        return self._first_row


class FakeSession:
    def __init__(self, results: list[FakeResult]) -> None:
        self._results = results
        self.executed: list[object] = []
        self.added: list[object] = []
        self.flush_count = 0
        self.commit_count = 0

    async def execute(self, statement: object) -> FakeResult:
        self.executed.append(statement)
        if self._results:
            return self._results.pop(0)
        return FakeResult()

    def add(self, item: object) -> None:
        if getattr(item, "id", None) is None:
            item.id = 100 + len(self.added)  # type: ignore[attr-defined]
        self.added.append(item)

    def add_all(self, items: list[object]) -> None:
        self.added.extend(items)

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:
        self.commit_count += 1


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_imports_rows() -> None:
    employer = EmployerModel(id=10, name="ACME", started_at=date(2026, 1, 31))
    session = FakeSession(
        [
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="SALARY_BASE"),
                    SimpleNamespace(id=2, code="PENSION_BASE"),
                ]
            ),
            FakeResult(scalar_one=employer),
            FakeResult(scalar_one=None),
            FakeResult(),
            FakeResult(),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.import_rows(
        [
            SimpleNamespace(
                employer="ACME",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                status="projected",
                concept_code="SALARY_BASE",
                amount_clp=Decimal("1000000"),
            ),
            SimpleNamespace(
                employer="ACME",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                status="projected",
                concept_code="PENSION_BASE",
                amount_clp=Decimal("100000"),
            ),
        ]
    )

    assert result.imported_periods == 1
    assert result.imported_items == 2
    assert result.periods[0].employer == "ACME"
    assert result.periods[0].status == "projected"
    assert session.flush_count == 1
    assert session.commit_count == 2
    assert any(isinstance(item, PayrollPeriodModel) for item in session.added)
    assert sum(isinstance(item, PayrollItemModel) for item in session.added) == 2


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_returns_empty_result_for_no_rows() -> None:
    session = FakeSession([])
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.import_rows([])

    assert result.imported_periods == 0
    assert result.imported_items == 0
    assert result.periods == []


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_creates_employer_and_replaces_existing_period_items() -> None:
    existing_period = PayrollPeriodModel(
        id=50,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 15),
        status=PayrollStatus.PROJECTED,
    )
    session = FakeSession(
        [
            FakeResult(scalar_rows=[SimpleNamespace(id=1, code="SALARY_BASE")]),
            FakeResult(scalar_one=None),
            FakeResult(scalar_one=existing_period),
            FakeResult(),
            FakeResult(),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.import_rows(
        [
            SimpleNamespace(
                employer="NewCo",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                status="actual",
                concept_code="SALARY_BASE",
                amount_clp=Decimal("1000000"),
            )
        ]
    )

    created_employers = [item for item in session.added if isinstance(item, EmployerModel)]
    assert len(created_employers) == 1
    assert existing_period.payment_date == date(2026, 1, 31)
    assert existing_period.status is PayrollStatus.ACTUAL
    assert result.periods[0].status == "actual"
    assert any("DELETE FROM payroll_items" in str(statement) for statement in session.executed)


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_rejects_unknown_concepts() -> None:
    session = FakeSession([FakeResult(scalar_rows=[])])
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Unknown payroll concepts"):
        await repository.import_rows(
            [
                SimpleNamespace(
                    employer="ACME",
                    period_year=2026,
                    period_month=1,
                    payment_date=date(2026, 1, 31),
                    status="projected",
                    concept_code="UNKNOWN",
                    amount_clp=Decimal("1"),
                )
            ]
        )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_builds_contribution_context() -> None:
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.PROJECTED,
    )
    pension_institution = PensionInstitutionModel(
        id=1,
        code="AFP_UNO",
        name="AFP Uno",
        mandatory_rate=Decimal("0.10"),
        is_active=True,
    )
    pension_plan = PensionPlanModel(
        id=11,
        institution_id=1,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        additional_rate=Decimal("0.0127"),
    )
    health_institution = HealthInstitutionModel(
        id=2,
        code="FONASA",
        name="Fonasa",
        kind=HealthInstitutionKind.FONASA,
        mandatory_rate=Decimal("0.07"),
        is_active=True,
    )
    health_plan = HealthPlanModel(
        id=22,
        institution_id=2,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        plan_name="Base",
        contracted_uf=Decimal("0"),
    )
    cap = ContributionCapModel(
        id=33,
        cap_type=ContributionCapType.PENSION_HEALTH,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        value_uf=Decimal("90.0000"),
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(first_row=(pension_plan, pension_institution)),
            FakeResult(first_row=(health_plan, health_institution)),
            FakeResult(scalar_one=cap),
            FakeResult(scalar_one=Decimal("1250000")),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.get_contribution_context(
        SimpleNamespace(period_id=5, pension_plan_id=11, health_plan_id=22)
    )

    assert result.period_id == 5
    assert result.taxable_income_clp == Decimal("1250000")
    assert result.pension_plan.institution.code == "AFP_UNO"
    assert result.health_plan.institution.kind is HealthInstitutionKind.FONASA
    assert result.cap.value_uf == Decimal("90.0000")


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_rejects_missing_period_for_contribution_context() -> None:
    repository = SqlAlchemyPayrollRepository(FakeSession([FakeResult(scalar_one=None)]))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Payroll period 9 was not found."):
        await repository.get_contribution_context(
            SimpleNamespace(period_id=9, pension_plan_id=1, health_plan_id=2)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("results", "message"),
    [
        (
            [
                FakeResult(
                    scalar_one=PayrollPeriodModel(
                        id=1,
                        employer_id=1,
                        period_year=2026,
                        period_month=1,
                        payment_date=date(2026, 1, 31),
                        status=PayrollStatus.PROJECTED,
                    )
                ),
                FakeResult(first_row=None),
            ],
            "Pension plan 1 was not found.",
        ),
        (
            [
                FakeResult(
                    scalar_one=PayrollPeriodModel(
                        id=1,
                        employer_id=1,
                        period_year=2026,
                        period_month=1,
                        payment_date=date(2026, 1, 31),
                        status=PayrollStatus.PROJECTED,
                    )
                ),
                FakeResult(
                    first_row=(
                        PensionPlanModel(
                            id=1,
                            institution_id=1,
                            valid_from=date(2026, 1, 1),
                            valid_to=None,
                            additional_rate=Decimal("0"),
                        ),
                        PensionInstitutionModel(
                            id=1,
                            code="AFP_UNO",
                            name="AFP Uno",
                            mandatory_rate=Decimal("0.10"),
                            is_active=True,
                        ),
                    )
                ),
                FakeResult(first_row=None),
            ],
            "Health plan 2 was not found.",
        ),
        (
            [
                FakeResult(
                    scalar_one=PayrollPeriodModel(
                        id=1,
                        employer_id=1,
                        period_year=2026,
                        period_month=1,
                        payment_date=date(2026, 1, 31),
                        status=PayrollStatus.PROJECTED,
                    )
                ),
                FakeResult(
                    first_row=(
                        PensionPlanModel(
                            id=1,
                            institution_id=1,
                            valid_from=date(2026, 1, 1),
                            valid_to=None,
                            additional_rate=Decimal("0"),
                        ),
                        PensionInstitutionModel(
                            id=1,
                            code="AFP_UNO",
                            name="AFP Uno",
                            mandatory_rate=Decimal("0.10"),
                            is_active=True,
                        ),
                    )
                ),
                FakeResult(
                    first_row=(
                        HealthPlanModel(
                            id=2,
                            institution_id=2,
                            valid_from=date(2026, 1, 1),
                            valid_to=None,
                            plan_name="Base",
                            contracted_uf=Decimal("0"),
                        ),
                        HealthInstitutionModel(
                            id=2,
                            code="FONASA",
                            name="Fonasa",
                            kind=HealthInstitutionKind.FONASA,
                            mandatory_rate=Decimal("0.07"),
                            is_active=True,
                        ),
                    )
                ),
                FakeResult(scalar_one=None),
            ],
            "No contribution cap was found for 2026-01-31.",
        ),
    ],
)
async def test_sqlalchemy_payroll_repository_rejects_missing_contribution_inputs(
    results: list[FakeResult],
    message: str,
) -> None:
    repository = SqlAlchemyPayrollRepository(FakeSession(results))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=message):
        await repository.get_contribution_context(
            SimpleNamespace(period_id=1, pension_plan_id=1, health_plan_id=2)
        )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_saves_computed_contributions() -> None:
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.PROJECTED,
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="PENSION_BASE"),
                    SimpleNamespace(id=2, code="PENSION_ADDITIONAL"),
                    SimpleNamespace(id=3, code="HEALTH_BASE"),
                    SimpleNamespace(id=4, code="HEALTH_ADDITIONAL_UF"),
                ]
            ),
            FakeResult(),
            FakeResult(),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.save_computed_contributions(
        SimpleNamespace(
            period_id=5,
            pension_plan_id=11,
            health_plan_id=22,
            pension=PensionContribution(
                institution_code="AFP_UNO",
                taxable_clp=Decimal("1000000"),
                cap_clp=Decimal("3000000"),
                capped_base_clp=Decimal("1000000"),
                base_amount_clp=Decimal("100000"),
                additional_amount_clp=Decimal("12700"),
            ),
            health=HealthContribution(
                institution_code="FONASA",
                institution_kind=HealthInstitutionKind.FONASA,
                taxable_clp=Decimal("1000000"),
                cap_clp=Decimal("3000000"),
                capped_base_clp=Decimal("1000000"),
                base_amount_clp=Decimal("70000"),
                contracted_uf=Decimal("0"),
                contracted_clp=Decimal("0"),
                additional_amount_clp=Decimal("0"),
            ),
        )
    )

    assert result.period_id == 5
    assert period.pension_plan_id == 11
    assert period.health_plan_id == 22
    assert sum(isinstance(item, PayrollItemModel) for item in session.added) == 4
    assert session.commit_count == 2
    assert any("DELETE FROM payroll_items" in str(statement) for statement in session.executed)


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_rejects_missing_period_when_saving_contributions() -> None:
    repository = SqlAlchemyPayrollRepository(FakeSession([FakeResult(scalar_one=None)]))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Payroll period 5 was not found."):
        await repository.save_computed_contributions(
            SimpleNamespace(period_id=5, pension_plan_id=1, health_plan_id=2, pension=object(), health=object())
        )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_rejects_missing_concepts_when_saving_contributions() -> None:
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.PROJECTED,
    )
    repository = SqlAlchemyPayrollRepository(
        FakeSession(
            [
                FakeResult(scalar_one=period),
                FakeResult(scalar_rows=[SimpleNamespace(id=1, code="PENSION_BASE")]),
            ]
        )
    )  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Missing payroll concepts for computed contributions"):
        await repository.save_computed_contributions(
            SimpleNamespace(period_id=5, pension_plan_id=1, health_plan_id=2, pension=object(), health=object())
        )


@pytest.mark.asyncio
async def test_api_dependencies_build_payroll_repository_and_use_case(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = object()
    exited = False

    class FakeSessionManager:
        async def __aenter__(self) -> object:
            return fake_session

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            nonlocal exited
            exited = True

    monkeypatch.setattr(dependencies, "SessionLocal", lambda: FakeSessionManager())

    iterator: AsyncIterator[object] = dependencies.get_session()
    assert await anext(iterator) is fake_session
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    assert exited is True

    repository = dependencies.get_payroll_repository(fake_session)  # type: ignore[arg-type]
    use_case = dependencies.get_import_payroll_use_case(repository)
    compute_use_case = dependencies.get_compute_contributions_use_case(repository)

    assert isinstance(repository, SqlAlchemyPayrollRepository)
    assert isinstance(use_case, ImportPayroll)
    assert compute_use_case.__class__.__name__ == "ComputeContributions"


def test_payroll_models_are_declared() -> None:
    assert EmployerModel.__tablename__ == "employers"
    assert PayrollPeriodModel.__tablename__ == "payroll_periods"
    assert PayrollItemModel.__tablename__ == "payroll_items"
    assert PayrollStatus.ACTUAL.value == "actual"
