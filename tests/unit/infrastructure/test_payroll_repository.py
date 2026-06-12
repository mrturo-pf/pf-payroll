"""Tests for test payroll repository."""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.application.use_cases.assign_plans import AssignPlans
from payroll.application.use_cases.generate_payroll_report import GeneratePayrollReport
from payroll.application.use_cases.review_payroll_period import ReviewPayrollPeriod
from payroll.domain.contributions import (
    HealthContribution,
    HealthInstitutionKind,
    PensionContribution,
)
from payroll.domain.contributions import (
    EmploymentContractKind,
    UnemploymentContribution,
)
from payroll.domain.taxes import IncomeTaxBracket
from payroll.infrastructure.db.models import (
    EmployerModel,
    PayrollItemModel,
    PayrollPeriodHealthPlanModel,
    PayrollPeriodModel,
    PayrollSummaryModel,
)
from payroll.infrastructure.db.models.payroll import (
    EmployerFixedDayRoll,
    EmployerPaymentDateRule,
    PayrollStatus,
)
from payroll.infrastructure.db.models.reference_data import (
    ContributionCapModel,
    ContributionCapType,
    HealthInstitutionModel,
    HealthPlanModel,
    IncomeTaxBracketModel,
    PensionInstitutionModel,
    PensionPlanModel,
)
from payroll.infrastructure.db.repositories.payroll_repository import (
    SqlAlchemyPayrollRepository,
)
from payroll.infrastructure.db.repositories.payroll_repository_shared import (
    _build_fx_provider,
    build_net_pay_warning,
    get_last_day_of_month,
    predict_next_period_net_pay,
)
from payroll.interfaces.api import dependencies
from payroll.shared.dates import add_months, resolve_payment_date


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

    def __init__(
        self,
        scalar_rows: list[object] | None = None,
        scalar_one: object | None = None,
        first_row: object | None = None,
        joined_rows: list[tuple[object, object]] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._scalar_rows = scalar_rows or []
        self._scalar_one = scalar_one
        self._first_row = first_row
        self._joined_rows = joined_rows or []

    def scalars(self) -> FakeScalarResult:
        """Handle scalars."""
        return FakeScalarResult(self._scalar_rows)

    def scalar_one_or_none(self) -> object | None:
        """Handle scalar one or none."""
        return self._scalar_one

    def scalar_one(self) -> object:
        """Handle scalar one."""
        return self._scalar_one

    def first(self) -> object | None:
        """Handle first."""
        return self._first_row

    def all(self) -> list[tuple[object, object]]:
        """Handle all."""
        return self._joined_rows


class FakeSession:
    """Test double for Session."""

    def __init__(self, results: list[FakeResult]) -> None:
        """Initialize the instance."""
        self._results = results
        self.executed: list[object] = []
        self.added: list[object] = []
        self.flush_count = 0
        self.commit_count = 0

    async def execute(self, statement: object) -> FakeResult:
        """Handle execute."""
        self.executed.append(statement)
        if self._results:
            return self._results.pop(0)
        return FakeResult()

    def add(self, item: object) -> None:
        """Handle add."""
        if getattr(item, "id", None) is None:
            item.id = 100 + len(self.added)  # type: ignore[attr-defined]
        self.added.append(item)

    def add_all(self, items: list[object]) -> None:
        """Handle add all."""
        self.added.extend(items)

    async def flush(self) -> None:
        """Handle flush."""
        self.flush_count += 1

    async def commit(self) -> None:
        """Handle commit."""
        self.commit_count += 1


class FakeFxRateProvider:
    """Test double for FX provider lookups."""

    def __init__(self, rates_by_date: dict[date, Decimal | None] | None = None) -> None:
        """Initialize the instance."""
        self._rates_by_date = rates_by_date or {}

    async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
        """Handle fetch rate."""
        if currency_code != "UF":
            return None
        return self._rates_by_date.get(on)


def build_period(
    *,
    period_id: int = 5,
    employer_id: int = 10,
    payment_date: date = date(2026, 1, 31),
    status: PayrollStatus = PayrollStatus.PROJECTED,
    employment_contract_kind: EmploymentContractKind = (
        EmploymentContractKind.INDEFINITE
    ),
) -> PayrollPeriodModel:
    """Build a payroll period model for repository tests."""
    return PayrollPeriodModel(
        id=period_id,
        employer_id=employer_id,
        period_year=payment_date.year,
        period_month=payment_date.month,
        payment_date=payment_date,
        status=status,
        employment_contract_kind=employment_contract_kind,
    )


def build_pension_pair(
    *,
    plan_id: int = 11,
    institution_id: int = 1,
    code: str = "AFP_UNO",
    name: str = "AFP Uno",
    additional_rate: Decimal = Decimal("0.0127"),
    active: bool = True,
) -> tuple[PensionPlanModel, PensionInstitutionModel]:
    """Build a pension plan and institution pair."""
    institution = PensionInstitutionModel(
        id=institution_id,
        code=code,
        name=name,
        mandatory_rate=Decimal("0.10"),
        is_active=active,
    )
    plan = PensionPlanModel(
        id=plan_id,
        institution_id=institution_id,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        additional_rate=additional_rate,
    )
    return plan, institution


def build_health_pair(
    *,
    plan_id: int = 22,
    institution_id: int = 2,
    code: str = "FONASA",
    name: str = "Fonasa",
    kind: HealthInstitutionKind = HealthInstitutionKind.FONASA,
    active: bool = True,
    contracted_uf: Decimal = Decimal("0"),
    plan_name: str = "Base",
) -> tuple[HealthPlanModel, HealthInstitutionModel]:
    """Build a health plan and institution pair."""
    institution = HealthInstitutionModel(
        id=institution_id,
        code=code,
        name=name,
        kind=kind,
        mandatory_rate=Decimal("0.07"),
        is_active=active,
    )
    plan = HealthPlanModel(
        id=plan_id,
        institution_id=institution_id,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        plan_name=plan_name,
        contracted_uf=contracted_uf,
    )
    return plan, institution


def build_contribution_cap(
    *,
    cap_id: int,
    cap_type: ContributionCapType,
    value_uf: Decimal,
) -> ContributionCapModel:
    """Build a contribution cap model."""
    return ContributionCapModel(
        id=cap_id,
        cap_type=cap_type,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        value_uf=value_uf,
    )


def build_contribution_context_results(
    *,
    period: PayrollPeriodModel,
    pension_pair: tuple[PensionPlanModel, PensionInstitutionModel],
    health_pair: tuple[HealthPlanModel, HealthInstitutionModel],
    taxable_income: Decimal = Decimal("1250000"),
    health_plan_ids: list[int] | None = None,
    period_health_pairs: list[tuple[HealthPlanModel, HealthInstitutionModel]]
    | None = None,
) -> list[FakeResult]:
    """Build fake DB result sequence for contribution context queries."""
    resolved_health_plan_ids = (
        [int(health_pair[0].id)] if health_plan_ids is None else health_plan_ids
    )
    results = [
        FakeResult(scalar_one=period),
        FakeResult(first_row=pension_pair),
        FakeResult(first_row=health_pair),
        FakeResult(
            scalar_one=build_contribution_cap(
                cap_id=33,
                cap_type=ContributionCapType.PENSION_HEALTH,
                value_uf=Decimal("90.0000"),
            )
        ),
        FakeResult(
            scalar_one=build_contribution_cap(
                cap_id=34,
                cap_type=ContributionCapType.UNEMPLOYMENT,
                value_uf=Decimal("135.0000"),
            )
        ),
        FakeResult(scalar_one=taxable_income),
        FakeResult(scalar_rows=resolved_health_plan_ids),
    ]
    resolved_period_health_pairs = (
        [health_pair for _ in resolved_health_plan_ids]
        if period_health_pairs is None
        else period_health_pairs
    )
    for period_health_pair in resolved_period_health_pairs:
        results.append(FakeResult(first_row=period_health_pair))
    return results


def build_import_row(**overrides: object) -> SimpleNamespace:
    """Build a default import row payload for repository import tests."""
    payload = {
        "employer": "ACME",
        "period_year": 2026,
        "period_month": 1,
        "payment_date": date(2026, 1, 31),
        "status": "actual",
        "employment_contract_kind": EmploymentContractKind.INDEFINITE,
        "concept_code": "SALARY_BASE",
        "amount_clp": Decimal("1000000"),
        "declared_net_pay_clp": None,
        "expected_net_pay_clp": None,
        "net_pay_difference_clp": None,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_build_net_pay_warning_reports_final_mismatch() -> None:
    """Test final net pay mismatch warning content."""
    assert build_net_pay_warning(
        Decimal("1000"),
        Decimal("900"),
        Decimal("100"),
    ) == (
        "Declared net_pay does not match the fully computed payroll totals. "
        "Difference: 100 CLP."
    )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_imports_rows() -> None:
    """Test sqlalchemy payroll repository imports rows."""
    employer = EmployerModel(id=10, name="ACME", started_at=date(2026, 1, 31))
    pension_institution = PensionInstitutionModel(
        id=1, code="AFP_TEST", name="AFP Test"
    )
    pension_plan = PensionPlanModel(
        id=1,
        institution_id=1,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        additional_rate=Decimal("0"),
    )
    health_institution = HealthInstitutionModel(
        id=1, code="FONASA", name="Fonasa", is_active=True
    )
    health_plan = HealthPlanModel(
        id=1,
        institution_id=1,
        valid_from=date(2026, 1, 1),
        valid_to=None,
    )
    session = FakeSession(
        [
            # Concept codes
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="SALARY_BASE"),
                    SimpleNamespace(id=2, code="PENSION_BASE"),
                ]
            ),
            # Pension plan deduction
            FakeResult(joined_rows=[(pension_plan, pension_institution)]),
            # Health plan deduction
            FakeResult(joined_rows=[(health_plan, health_institution)]),
            # Pension plan validation
            FakeResult(first_row=(pension_plan, pension_institution)),
            # Health plan validation
            FakeResult(first_row=(health_plan, health_institution)),
            # Employer lookup
            FakeResult(scalar_one=employer),
            # Check period exists
            FakeResult(scalar_one=None),
            # Other operations
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
                employment_contract_kind=EmploymentContractKind.INDEFINITE,
                concept_code="SALARY_BASE",
                amount_clp=Decimal("1000000"),
                declared_net_pay_clp=Decimal("950000"),
                expected_net_pay_clp=Decimal("900000"),
                net_pay_difference_clp=Decimal("50000"),
            ),
            SimpleNamespace(
                employer="ACME",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                status="projected",
                employment_contract_kind=EmploymentContractKind.INDEFINITE,
                concept_code="PENSION_BASE",
                amount_clp=Decimal("100000"),
                declared_net_pay_clp=Decimal("950000"),
                expected_net_pay_clp=Decimal("900000"),
                net_pay_difference_clp=Decimal("50000"),
            ),
        ]
    )

    assert result.imported_periods == 1
    assert result.imported_items == 2
    assert result.periods[0].employer == "ACME"
    assert result.periods[0].status == "projected"
    assert (
        result.periods[0].employment_contract_kind is EmploymentContractKind.INDEFINITE
    )
    assert result.periods[0].declared_net_pay_clp == Decimal("950000")
    assert result.periods[0].expected_net_pay_clp is None
    assert result.periods[0].net_pay_difference_clp is None
    assert result.periods[0].net_pay_warning == (
        "Declared net_pay will be reconciled after computed contributions "
        "and income tax are generated."
    )
    assert result.market_data_sync_request is not None
    assert result.market_data_sync_request.exchange_rate_dates == {
        "USD": [date(2026, 1, 31)],
        "EUR": [date(2026, 1, 31)],
        "UF": [date(2026, 1, 31)],
        "UTM": [date(2026, 1, 1)],
    }
    assert result.market_data_sync_request.economic_index_periods == {}
    assert session.flush_count == 1
    assert session.commit_count == 2
    assert any(isinstance(item, PayrollPeriodModel) for item in session.added)
    assert sum(isinstance(item, PayrollItemModel) for item in session.added) == 2


@pytest.mark.asyncio
async def test_sa_payroll_repository_assigns_plan_ids_from_import_rows() -> None:
    """Test import rows assign period plan ids when provided in the payload."""
    employer = EmployerModel(id=10, name="ACME", started_at=date(2026, 1, 31))
    pension_plan, pension_institution = build_pension_pair(
        plan_id=1,
        institution_id=5,
        code="AFP_MODEL",
        name="AFP Model",
        additional_rate=Decimal("0.0116"),
    )
    health_plan, health_institution = build_health_pair(
        plan_id=2,
        institution_id=6,
        contracted_uf=Decimal("5.42"),
    )
    session = FakeSession(
        [
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="SALARY_BASE"),
                    SimpleNamespace(id=2, code="PENSION_BASE"),
                ]
            ),
            FakeResult(first_row=(pension_plan, pension_institution)),
            FakeResult(first_row=(health_plan, health_institution)),
            FakeResult(scalar_one=employer),
            FakeResult(scalar_one=None),
            FakeResult(),
            FakeResult(),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    await repository.import_rows(
        [
            SimpleNamespace(
                employer="ACME",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                status="actual",
                employment_contract_kind=EmploymentContractKind.INDEFINITE,
                concept_code="SALARY_BASE",
                amount_clp=Decimal("1000000"),
                pension_plan_id=1,
                health_plan_id=2,
                declared_net_pay_clp=Decimal("950000"),
                expected_net_pay_clp=Decimal("900000"),
                net_pay_difference_clp=Decimal("50000"),
            ),
            SimpleNamespace(
                employer="ACME",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                status="actual",
                employment_contract_kind=EmploymentContractKind.INDEFINITE,
                concept_code="PENSION_BASE",
                amount_clp=Decimal("100000"),
                pension_plan_id=1,
                health_plan_id=2,
                declared_net_pay_clp=Decimal("950000"),
                expected_net_pay_clp=Decimal("900000"),
                net_pay_difference_clp=Decimal("50000"),
            ),
        ]
    )

    created_period = next(
        item for item in session.added if isinstance(item, PayrollPeriodModel)
    )
    assert created_period.pension_plan_id == 1
    assert any(
        isinstance(item, PayrollPeriodHealthPlanModel)
        and item.health_plan_id == 2
        and item.period_id == created_period.id
        for item in session.added
    )


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_inconsistent_period_plan_ids() -> None:
    """Test import rows reject inconsistent plan ids within one period."""
    session = FakeSession(
        [
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="SALARY_BASE"),
                    SimpleNamespace(id=2, code="PENSION_BASE"),
                ]
            ),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Inconsistent pension_plan_id"):
        await repository.import_rows(
            [
                SimpleNamespace(
                    employer="ACME",
                    period_year=2026,
                    period_month=1,
                    payment_date=date(2026, 1, 31),
                    status="actual",
                    employment_contract_kind=EmploymentContractKind.INDEFINITE,
                    concept_code="SALARY_BASE",
                    amount_clp=Decimal("1000000"),
                    pension_plan_id=1,
                    health_plan_id=2,
                    declared_net_pay_clp=None,
                    expected_net_pay_clp=None,
                    net_pay_difference_clp=None,
                ),
                SimpleNamespace(
                    employer="ACME",
                    period_year=2026,
                    period_month=1,
                    payment_date=date(2026, 1, 31),
                    status="actual",
                    employment_contract_kind=EmploymentContractKind.INDEFINITE,
                    concept_code="PENSION_BASE",
                    amount_clp=Decimal("100000"),
                    pension_plan_id=3,
                    health_plan_id=2,
                    declared_net_pay_clp=None,
                    expected_net_pay_clp=None,
                    net_pay_difference_clp=None,
                ),
            ]
        )


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_inconsistent_period_health_plan_ids() -> (
    None
):
    """Test import rows reject inconsistent health plans within one period."""
    session = FakeSession(
        [
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="SALARY_BASE"),
                    SimpleNamespace(id=2, code="PENSION_BASE"),
                ]
            ),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Inconsistent health_plan_id"):
        await repository.import_rows(
            [
                SimpleNamespace(
                    employer="ACME",
                    period_year=2026,
                    period_month=1,
                    payment_date=date(2026, 1, 31),
                    status="actual",
                    employment_contract_kind=EmploymentContractKind.INDEFINITE,
                    concept_code="SALARY_BASE",
                    amount_clp=Decimal("1000000"),
                    pension_plan_id=1,
                    health_plan_ids=(2, 3),
                    declared_net_pay_clp=None,
                    expected_net_pay_clp=None,
                    net_pay_difference_clp=None,
                ),
                SimpleNamespace(
                    employer="ACME",
                    period_year=2026,
                    period_month=1,
                    payment_date=date(2026, 1, 31),
                    status="actual",
                    employment_contract_kind=EmploymentContractKind.INDEFINITE,
                    concept_code="PENSION_BASE",
                    amount_clp=Decimal("100000"),
                    pension_plan_id=1,
                    health_plan_ids=(2, 4),
                    declared_net_pay_clp=None,
                    expected_net_pay_clp=None,
                    net_pay_difference_clp=None,
                ),
            ]
        )


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_partial_period_plan_assignment() -> None:
    """Test import rows require both plan ids together for the same period."""
    session = FakeSession(
        [
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="SALARY_BASE"),
                ]
            ),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(
        ValueError,
        match="Both pension_plan_id and health_plan_id must be provided together.",
    ):
        await repository.import_rows(
            [
                SimpleNamespace(
                    employer="ACME",
                    period_year=2026,
                    period_month=1,
                    payment_date=date(2026, 1, 31),
                    status="actual",
                    employment_contract_kind=EmploymentContractKind.INDEFINITE,
                    concept_code="SALARY_BASE",
                    amount_clp=Decimal("1000000"),
                    pension_plan_id=1,
                    health_plan_id=None,
                    declared_net_pay_clp=None,
                    expected_net_pay_clp=None,
                    net_pay_difference_clp=None,
                ),
            ]
        )


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_missing_pension_plan_deduction() -> None:
    """Test import rows raise error when no valid pension plan for date."""
    session = FakeSession(
        [
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="SALARY_BASE"),
                ]
            ),
            # Pension plan deduction returns empty list
            FakeResult(joined_rows=[]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(
        ValueError,
        match="No valid pension plan found for reference date",
    ):
        await repository.import_rows([build_import_row()])


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_missing_health_plans_deduction() -> None:
    """Test import rows raise error when no valid health plans for reference date."""
    pension_institution = PensionInstitutionModel(
        id=1, code="AFP_TEST", name="AFP Test"
    )
    pension_plan = PensionPlanModel(
        id=1,
        institution_id=1,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        additional_rate=Decimal("0"),
    )
    session = FakeSession(
        [
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="SALARY_BASE"),
                ]
            ),
            # Pension plan deduction returns valid plan
            FakeResult(joined_rows=[(pension_plan, pension_institution)]),
            # Health plan deduction returns empty list
            FakeResult(joined_rows=[]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(
        ValueError,
        match="No valid health plans found for reference date",
    ):
        await repository.import_rows([build_import_row()])

    """Test import rows update existing period with provided plan ids."""
    employer = EmployerModel(id=10, name="ACME", started_at=date(2026, 1, 31))
    existing_period = PayrollPeriodModel(
        id=50,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.PROJECTED,
    )
    pension_plan, pension_institution = build_pension_pair(
        plan_id=1,
        institution_id=5,
        code="AFP_MODEL",
        name="AFP Model",
        additional_rate=Decimal("0.0116"),
    )
    health_plan, health_institution = build_health_pair(
        plan_id=2,
        institution_id=6,
        contracted_uf=Decimal("5.42"),
    )
    session = FakeSession(
        [
            FakeResult(scalar_rows=[SimpleNamespace(id=1, code="SALARY_BASE")]),
            FakeResult(first_row=(pension_plan, pension_institution)),
            FakeResult(first_row=(health_plan, health_institution)),
            FakeResult(scalar_one=employer),
            FakeResult(scalar_one=existing_period),
            FakeResult(),
            FakeResult(),
            FakeResult(),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    await repository.import_rows(
        [
            SimpleNamespace(
                employer="ACME",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                status="actual",
                employment_contract_kind=EmploymentContractKind.INDEFINITE,
                concept_code="SALARY_BASE",
                amount_clp=Decimal("1000000"),
                pension_plan_id=1,
                health_plan_id=2,
                declared_net_pay_clp=None,
                expected_net_pay_clp=None,
                net_pay_difference_clp=None,
            ),
        ]
    )
    assert existing_period.pension_plan_id == 1
    assert existing_period.pension_plan_id == 1
    assert any(
        isinstance(item, PayrollPeriodHealthPlanModel)
        and item.health_plan_id == 2
        and item.period_id == existing_period.id
        for item in session.added
    )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_returns_empty_result_for_no_rows() -> None:
    """Test sqlalchemy payroll repository returns empty result for no rows."""
    session = FakeSession([])
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.import_rows([])

    assert result.imported_periods == 0
    assert result.imported_items == 0
    assert result.periods == []


@pytest.mark.asyncio
async def test_sa_payroll_repository_closes_previous_open_ended_employer() -> None:
    """Test creating an employer closes previous open-ended employers."""
    previous_employer = EmployerModel(
        id=9,
        name="PreviousCo",
        started_at=date(2025, 1, 1),
    )
    pension_plan = PensionPlanModel(
        id=1,
        institution_id=5,
        valid_from=date(2024, 11, 1),
        valid_to=None,
        additional_rate=Decimal("0"),
    )
    pension_institution = PensionInstitutionModel(
        id=5, code="AFP_TEST", name="AFP Test"
    )
    health_plan = HealthPlanModel(
        id=1,
        institution_id=6,
        valid_from=date(2024, 11, 1),
        valid_to=None,
    )
    health_institution = HealthInstitutionModel(
        id=6, code="FONASA", name="Fonasa", is_active=True
    )
    session = FakeSession(
        [
            FakeResult(scalar_rows=[SimpleNamespace(id=1, code="SALARY_BASE")]),
            # Pension plan deduction
            FakeResult(joined_rows=[(pension_plan, pension_institution)]),
            # Health plan deduction
            FakeResult(joined_rows=[(health_plan, health_institution)]),
            # Pension plan validation
            FakeResult(first_row=(pension_plan, pension_institution)),
            # Health plan validation
            FakeResult(first_row=(health_plan, health_institution)),
            # Employer lookup - NewCo doesn't exist yet
            FakeResult(scalar_one=None),
            # Close overlapping open-ended employers
            FakeResult(scalar_rows=[previous_employer]),
            # Period lookup - new period doesn't exist yet
            FakeResult(scalar_one=None),
            FakeResult(),
            FakeResult(),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    await repository.import_rows(
        [
            SimpleNamespace(
                employer="NewCo",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                status="actual",
                employment_contract_kind=EmploymentContractKind.FIXED_TERM,
                concept_code="SALARY_BASE",
                amount_clp=Decimal("1000000"),
                declared_net_pay_clp=None,
                expected_net_pay_clp=None,
                net_pay_difference_clp=None,
            )
        ]
    )

    created_employer = next(
        item
        for item in session.added
        if isinstance(item, EmployerModel) and item.name == "NewCo"
    )
    assert previous_employer.ended_at == date(2026, 1, 30)
    assert (
        created_employer.payment_date_rule
        is EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH
    )
    assert created_employer.payment_month_offset == 0
    assert created_employer.payment_day_of_month is None
    assert created_employer.payment_business_day_offset == 0
    assert created_employer.payment_calendar_day_offset == 0
    assert created_employer.payment_effective_on_processing_next_day is False
    assert (
        created_employer.payment_fixed_day_roll
        is EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY
    )


@pytest.mark.asyncio
async def test_sa_payroll_repository_updates_existing_employer_started_at() -> None:
    """Test importing older periods updates the employer start date."""
    employer = EmployerModel(
        id=10,
        name="ACME",
        started_at=date(2026, 2, 28),
    )
    previous_employer = EmployerModel(
        id=9,
        name="PreviousCo",
        started_at=date(2025, 1, 1),
    )
    pension_plan = PensionPlanModel(
        id=1,
        institution_id=5,
        valid_from=date(2024, 11, 1),
        valid_to=None,
        additional_rate=Decimal("0"),
    )
    pension_institution = PensionInstitutionModel(
        id=5, code="AFP_TEST", name="AFP Test"
    )
    health_plan = HealthPlanModel(
        id=1,
        institution_id=6,
        valid_from=date(2024, 11, 1),
        valid_to=None,
    )
    health_institution = HealthInstitutionModel(
        id=6, code="FONASA", name="Fonasa", is_active=True
    )
    session = FakeSession(
        [
            FakeResult(scalar_rows=[SimpleNamespace(id=1, code="SALARY_BASE")]),
            # Pension plan deduction
            FakeResult(joined_rows=[(pension_plan, pension_institution)]),
            # Health plan deduction
            FakeResult(joined_rows=[(health_plan, health_institution)]),
            # Pension plan validation
            FakeResult(first_row=(pension_plan, pension_institution)),
            # Health plan validation
            FakeResult(first_row=(health_plan, health_institution)),
            # Employer lookup - return existing employer
            FakeResult(scalar_one=employer),
            # Close overlapping open-ended employers
            FakeResult(scalar_rows=[previous_employer]),
            # Period lookup - new period doesn't exist yet
            FakeResult(scalar_one=None),
            FakeResult(),
            FakeResult(),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    await repository.import_rows(
        [
            SimpleNamespace(
                employer="ACME",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                status="actual",
                employment_contract_kind=EmploymentContractKind.FIXED_TERM,
                concept_code="SALARY_BASE",
                amount_clp=Decimal("1000000"),
                declared_net_pay_clp=None,
                expected_net_pay_clp=None,
                net_pay_difference_clp=None,
            )
        ]
    )

    assert employer.started_at == date(2026, 1, 31)
    assert previous_employer.ended_at == date(2026, 1, 30)


@pytest.mark.asyncio
async def test_sa_payroll_repository_creates_employer_and_replaces_period_items() -> (
    None
):
    """Test creating an employer and replacing existing period items."""
    existing_period = PayrollPeriodModel(
        id=50,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 15),
        status=PayrollStatus.PROJECTED,
    )
    pension_plan = PensionPlanModel(
        id=1,
        institution_id=5,
        valid_from=date(2024, 11, 1),
        valid_to=None,
        additional_rate=Decimal("0"),
    )
    pension_institution = PensionInstitutionModel(
        id=5, code="AFP_TEST", name="AFP Test"
    )
    health_plan = HealthPlanModel(
        id=1,
        institution_id=6,
        valid_from=date(2024, 11, 1),
        valid_to=None,
    )
    health_institution = HealthInstitutionModel(
        id=6, code="FONASA", name="Fonasa", is_active=True
    )
    session = FakeSession(
        [
            FakeResult(scalar_rows=[SimpleNamespace(id=1, code="SALARY_BASE")]),
            # Pension plan deduction
            FakeResult(joined_rows=[(pension_plan, pension_institution)]),
            # Health plan deduction
            FakeResult(joined_rows=[(health_plan, health_institution)]),
            # Pension plan validation
            FakeResult(first_row=(pension_plan, pension_institution)),
            # Health plan validation
            FakeResult(first_row=(health_plan, health_institution)),
            # Employer lookup - NewCo doesn't exist yet
            FakeResult(scalar_one=None),
            # Close overlapping open-ended employers
            FakeResult(scalar_rows=[]),
            # Period lookup
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
                employment_contract_kind=EmploymentContractKind.FIXED_TERM,
                concept_code="SALARY_BASE",
                amount_clp=Decimal("1000000"),
                declared_net_pay_clp=None,
                expected_net_pay_clp=None,
                net_pay_difference_clp=None,
            )
        ]
    )

    created_employers = [
        item for item in session.added if isinstance(item, EmployerModel)
    ]
    assert len(created_employers) == 1
    assert existing_period.payment_date == date(2026, 1, 31)
    assert existing_period.status is PayrollStatus.ACTUAL
    assert existing_period.employment_contract_kind is EmploymentContractKind.FIXED_TERM
    assert result.periods[0].status == "actual"
    assert any(
        "DELETE FROM payroll_items" in str(statement) for statement in session.executed
    )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_rejects_unknown_concepts() -> None:
    """Test sqlalchemy payroll repository rejects unknown concepts."""
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
                    employment_contract_kind=EmploymentContractKind.INDEFINITE,
                    concept_code="UNKNOWN",
                    amount_clp=Decimal("1"),
                )
            ]
        )


@pytest.mark.asyncio
async def test_sa_payroll_repository_skips_sync_when_market_data_is_complete() -> None:
    """Test imported periods skip sync scheduling when coverage is complete."""
    session = FakeSession(
        [
            FakeResult(scalar_one=None),
            FakeResult(scalar_rows=[date(2026, 1, 31)]),
            FakeResult(scalar_rows=[date(2026, 1, 31)]),
            FakeResult(scalar_rows=[date(2026, 1, 31)]),
            FakeResult(scalar_rows=[date(2026, 1, 1)]),
            FakeResult(joined_rows=[(2026, 1)]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.PROJECTED,
    )

    assert await repository._build_market_data_sync_request([period]) is None


@pytest.mark.asyncio
async def test_sa_payroll_repository_builds_sync_request_with_prior_gaps() -> None:
    """Test imported periods enqueue only the missing gaps after the previous period."""
    session = FakeSession(
        [
            FakeResult(
                scalar_one=PayrollPeriodModel(
                    id=4,
                    employer_id=10,
                    period_year=2025,
                    period_month=12,
                    payment_date=date(2025, 12, 30),
                    status=PayrollStatus.PROJECTED,
                )
            ),
            FakeResult(
                scalar_rows=[date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 2)]
            ),
            FakeResult(
                scalar_rows=[date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 1)]
            ),
            FakeResult(
                scalar_rows=[date(2025, 12, 30), date(2026, 1, 1), date(2026, 1, 2)]
            ),
            FakeResult(scalar_rows=[date(2025, 12, 1)]),
            FakeResult(joined_rows=[(2025, 12)]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 2),
        status=PayrollStatus.PROJECTED,
    )

    result = await repository._build_market_data_sync_request([period])

    assert result is not None
    assert result.exchange_rate_dates == {
        "USD": [date(2026, 1, 1)],
        "EUR": [date(2026, 1, 2)],
        "UF": [date(2025, 12, 31), date(2026, 1, 31)],
        "UTM": [date(2026, 1, 1)],
    }
    assert result.economic_index_periods == {}


@pytest.mark.asyncio
async def test_sa_payroll_repository_omits_latest_ipc_only_gap() -> None:
    """Test sync request is skipped when only the latest IPC period is missing."""
    session = FakeSession(
        [
            FakeResult(scalar_one=None),
            FakeResult(scalar_rows=[date(2026, 1, 31)]),
            FakeResult(scalar_rows=[date(2026, 1, 31)]),
            FakeResult(scalar_rows=[date(2026, 1, 31), date(2026, 1, 1)]),
            FakeResult(scalar_rows=[date(2026, 1, 1)]),
            FakeResult(joined_rows=[]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.PROJECTED,
    )

    assert await repository._build_market_data_sync_request([period]) is None


@pytest.mark.asyncio
async def test_sa_payroll_repository_adds_current_period_uf_anchor_dates() -> None:
    """Test sync request adds UF month-end and today's anchor for current period."""
    today = date.today()
    month_start = date(today.year, today.month, 1)
    month_end = get_last_day_of_month(month_start)
    next_month_start = date(
        today.year + (1 if today.month == 12 else 0),
        1 if today.month == 12 else today.month + 1,
        1,
    )
    current_period = PayrollPeriodModel(
        id=50,
        employer_id=10,
        period_year=today.year,
        period_month=today.month,
        payment_date=month_start,
        status=PayrollStatus.PROJECTED,
        declared_net_pay_clp=Decimal("1"),
    )
    current_employer = EmployerModel(id=10, name="ACME", started_at=month_start)
    session = FakeSession(
        [
            FakeResult(scalar_one=None),
            FakeResult(scalar_rows=[month_start]),
            FakeResult(scalar_rows=[month_start]),
            FakeResult(scalar_rows=[month_start]),
            FakeResult(scalar_rows=[month_start]),
            FakeResult(joined_rows=[(today.year, today.month)]),
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_one=next_month_start),
            FakeResult(scalar_rows=[]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=today.year,
        period_month=today.month,
        payment_date=month_start,
        status=PayrollStatus.PROJECTED,
    )

    result = await repository._build_market_data_sync_request([period])

    assert result is not None
    assert result.exchange_rate_dates == {"UF": sorted({month_end, today})}
    assert result.economic_index_periods == {}


@pytest.mark.asyncio
async def test_sa_payroll_repository_skips_empty_uf_anchor() -> None:
    """Test current-period UF anchor helper skips empty period lists."""
    repository = SqlAlchemyPayrollRepository(FakeSession([]))  # type: ignore[arg-type]
    missing_dates_by_code: dict[str, set[date]] = {}

    await repository._collect_current_period_uf_anchor_dates(
        [],
        missing_dates_by_code,
    )

    assert missing_dates_by_code == {}


@pytest.mark.asyncio
async def test_sa_payroll_repository_resolves_end_month_without_next() -> None:
    """Test UF anchor uses resolved next period when no persisted next period exists."""
    today = date.today()
    month_start = date(today.year, today.month, 1)
    current_period = PayrollPeriodModel(
        id=50,
        employer_id=10,
        period_year=today.year,
        period_month=today.month,
        payment_date=month_start,
        status=PayrollStatus.PROJECTED,
        declared_net_pay_clp=Decimal("1"),
    )
    current_employer = EmployerModel(
        id=10,
        name="ACME",
        country_code="CL",
        started_at=month_start,
        payment_date_rule=EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH,
        payment_month_offset=0,
        payment_day_of_month=None,
        payment_business_day_offset=0,
        payment_calendar_day_offset=0,
        payment_effective_on_processing_next_day=False,
        payment_fixed_day_roll=EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY,
    )
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_one=None),
            FakeResult(scalar_rows=[]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]
    missing_dates_by_code: dict[str, set[date]] = {}

    await repository._collect_current_period_uf_anchor_dates(
        [current_period],
        missing_dates_by_code,
    )

    next_period_month = add_months(month_start, 1)
    next_period_start = resolve_payment_date(
        next_period_month.year,
        next_period_month.month,
        country_code="CL",
        payment_date_rule=EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH.value,
        payment_month_offset=0,
        payment_day_of_month=None,
        payment_business_day_offset=0,
        payment_calendar_day_offset=0,
        payment_effective_on_processing_next_day=False,
        payment_fixed_day_roll=EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY.value,
    )
    expected_anchor_month_end = get_last_day_of_month(
        next_period_start - timedelta(days=1)
    )
    assert missing_dates_by_code == {"UF": {today, expected_anchor_month_end}}


@pytest.mark.asyncio
async def test_sa_payroll_repository_handles_empty_gap_requests() -> None:
    """Test imported periods helper returns no missing entries for empty ranges."""
    repository = SqlAlchemyPayrollRepository(FakeSession([]))  # type: ignore[arg-type]

    assert await repository._build_market_data_sync_request([]) is None
    assert (
        await repository._list_missing_exchange_rate_dates(
            currency_code="UF",
            requested_dates=[],
        )
        == []
    )
    assert (
        await repository._list_missing_index_periods(
            code="IPC_CL",
            requested_periods=[],
        )
        == []
    )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_builds_contribution_context() -> None:
    """Test sqlalchemy payroll repository builds contribution context."""
    period = build_period()
    session = FakeSession(
        build_contribution_context_results(
            period=period,
            pension_pair=build_pension_pair(),
            health_pair=build_health_pair(),
        )
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.get_contribution_context(
        SimpleNamespace(period_id=5, pension_plan_id=11, health_plan_id=22)
    )

    assert result.period_id == 5
    assert result.taxable_income_clp == Decimal("1250000")
    assert result.pension_plan.institution.code == "AFP_UNO"
    assert result.health_plan.institution.kind is HealthInstitutionKind.FONASA
    assert result.employment_contract_kind is EmploymentContractKind.INDEFINITE
    assert result.cap.value_uf == Decimal("90.0000")
    assert result.unemployment_cap.value_uf == Decimal("135.0000")


@pytest.mark.asyncio
async def test_repository_allows_inactive_health_institution_for_history() -> None:
    """Test contribution context keeps inactive institutions for existing history."""
    period = build_period()
    session = FakeSession(
        build_contribution_context_results(
            period=period,
            pension_pair=build_pension_pair(),
            health_pair=build_health_pair(
                code="LEGACY",
                name="Legacy",
                active=False,
            ),
        )
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.get_contribution_context(
        SimpleNamespace(period_id=5, pension_plan_id=11, health_plan_id=22)
    )

    assert result.health_plan.institution.code == "LEGACY"


@pytest.mark.asyncio
async def test_repository_rejects_context_without_health_snapshots() -> None:
    """Test contribution context requires relation-table health plan snapshots."""
    period = build_period()
    session = FakeSession(
        build_contribution_context_results(
            period=period,
            pension_pair=build_pension_pair(),
            health_pair=build_health_pair(contracted_uf=Decimal("5.42")),
            health_plan_ids=[],
        )
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(
        ValueError,
        match="must have health plan snapshots assigned before computing contributions",
    ):
        await repository.get_contribution_context(
            SimpleNamespace(period_id=5, pension_plan_id=11, health_plan_id=22)
        )


@pytest.mark.asyncio
async def test_repository_sums_contracted_uf_for_multiple_period_health_plans() -> None:
    """Test contribution context sums contracted UF across period health plans."""
    period = build_period()
    session = FakeSession(
        build_contribution_context_results(
            period=period,
            pension_pair=build_pension_pair(),
            health_pair=build_health_pair(contracted_uf=Decimal("5.42")),
            health_plan_ids=[22, 23],
            period_health_pairs=[
                build_health_pair(plan_id=22, contracted_uf=Decimal("5.42")),
                build_health_pair(
                    plan_id=23,
                    contracted_uf=Decimal("0.91"),
                    plan_name="GES",
                ),
            ],
        )
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.get_contribution_context(
        SimpleNamespace(period_id=5, pension_plan_id=11, health_plan_id=22)
    )

    assert result.health_plan.contracted_uf == Decimal("6.33")


@pytest.mark.asyncio
async def test_repository_rejects_contribution_context_health_plan_not_in_period() -> (
    None
):
    """Test contribution context rejects health plans outside period snapshots."""
    period = build_period()
    session = FakeSession(
        build_contribution_context_results(
            period=period,
            pension_pair=build_pension_pair(),
            health_pair=build_health_pair(contracted_uf=Decimal("5.42")),
            health_plan_ids=[23],
        )
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(
        ValueError, match="does not match the period health plan snapshots"
    ):
        await repository.get_contribution_context(
            SimpleNamespace(period_id=5, pension_plan_id=11, health_plan_id=22)
        )


@pytest.mark.asyncio
async def test_repository_rejects_contribution_context_mixed_health_institutions() -> (
    None
):
    """Test contribution context rejects mixed institutions in period health plans."""
    period = build_period()
    session = FakeSession(
        build_contribution_context_results(
            period=period,
            pension_pair=build_pension_pair(),
            health_pair=build_health_pair(contracted_uf=Decimal("5.42")),
            health_plan_ids=[22, 23],
            period_health_pairs=[
                build_health_pair(plan_id=22, contracted_uf=Decimal("5.42")),
                build_health_pair(
                    plan_id=23,
                    institution_id=3,
                    code="ISAPRE_X",
                    name="Isapre X",
                    kind=HealthInstitutionKind.ISAPRE,
                    contracted_uf=Decimal("0.91"),
                    plan_name="Other",
                ),
            ],
        )
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="must belong to the same health institution"):
        await repository.get_contribution_context(
            SimpleNamespace(period_id=5, pension_plan_id=11, health_plan_id=22)
        )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_assigns_plans_to_period() -> None:
    """Test sqlalchemy payroll repository assigns plans to period."""
    period = build_period(employer_id=1, status=PayrollStatus.ACTUAL)
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(first_row=build_pension_pair()),
            FakeResult(first_row=build_health_pair()),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.assign_plans(
        SimpleNamespace(period_id=5, pension_plan_id=11, health_plan_id=22)
    )

    assert result.period_id == 5
    assert result.payment_date == date(2026, 1, 31)
    assert period.pension_plan_id == 11
    assert any(
        isinstance(item, PayrollPeriodHealthPlanModel)
        and item.period_id == period.id
        and item.health_plan_id == 22
        for item in session.added
    )
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_repository_rejects_assigning_inactive_health_institution() -> None:
    """Test assign plans rejects inactive health institutions."""
    period = build_period(employer_id=1, status=PayrollStatus.ACTUAL)
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(first_row=build_pension_pair()),
            FakeResult(
                first_row=build_health_pair(
                    code="LEGACY",
                    name="Legacy",
                    active=False,
                )
            ),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(
        ValueError,
        match="Health plan 22 belongs to inactive health institution LEGACY.",
    ):
        await repository.assign_plans(
            SimpleNamespace(period_id=5, pension_plan_id=11, health_plan_id=22)
        )


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_missing_period_for_contribution_ctx() -> (
    None
):
    """Test rejection when contribution context has no matching period."""
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
                FakeResult(
                    scalar_one=ContributionCapModel(
                        id=33,
                        cap_type=ContributionCapType.PENSION_HEALTH,
                        valid_from=date(2026, 1, 1),
                        valid_to=None,
                        value_uf=Decimal("90.0000"),
                    )
                ),
                FakeResult(scalar_one=None),
            ],
            "No unemployment contribution cap was found for 2026-01-31.",
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
                            valid_from=date(2026, 2, 1),
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
            ],
            "Pension plan 1 is not valid for 2026-01-31.",
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
                            valid_from=date(2025, 1, 1),
                            valid_to=date(2025, 12, 31),
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
            ],
            "Health plan 2 is not valid for 2026-01-31.",
        ),
    ],
)
async def test_sqlalchemy_payroll_repository_rejects_missing_contribution_inputs(
    results: list[FakeResult],
    message: str,
) -> None:
    """Test sqlalchemy payroll repository rejects missing contribution inputs."""
    repository = SqlAlchemyPayrollRepository(FakeSession(results))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=message):
        await repository.get_contribution_context(
            SimpleNamespace(period_id=1, pension_plan_id=1, health_plan_id=2)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("results", "message"),
    [
        ([FakeResult(scalar_one=None)], "Payroll period 9 was not found."),
        (
            [
                FakeResult(
                    scalar_one=PayrollPeriodModel(
                        id=9,
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
                        id=9,
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
    ],
)
async def test_sqlalchemy_payroll_repository_rejects_invalid_assign_plans_inputs(
    results: list[FakeResult],
    message: str,
) -> None:
    """Test sqlalchemy payroll repository rejects invalid assign plans inputs."""
    repository = SqlAlchemyPayrollRepository(FakeSession(results))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=message):
        await repository.assign_plans(
            SimpleNamespace(period_id=9, pension_plan_id=1, health_plan_id=2)
        )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_reviews_period() -> None:
    """Test sqlalchemy payroll repository reviews period."""
    period = PayrollPeriodModel(
        id=5,
        employer_id=1,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.ACTUAL,
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
        pension_plan_id=11,
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(scalar_rows=[22]),
            FakeResult(
                scalar_rows=[
                    "PENSION_BASE",
                    "PENSION_ADDITIONAL",
                    "HEALTH_BASE",
                    "HEALTH_ADDITIONAL_UF",
                    "UNEMPLOYMENT_INSURANCE",
                    "INCOME_TAX",
                ]
            ),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.review_period(SimpleNamespace(period_id=5))

    assert result.period_id == 5
    assert result.status == "reviewed"
    assert period.status is PayrollStatus.REVIEWED
    assert session.commit_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("period", "assigned_plan_ids", "present_codes", "message"),
    [
        (
            PayrollPeriodModel(
                id=5,
                employer_id=1,
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                status=PayrollStatus.ACTUAL,
                pension_plan_id=None,
            ),
            [],
            [],
            "must have pension and health plans assigned before review",
        ),
        (
            PayrollPeriodModel(
                id=5,
                employer_id=1,
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                status=PayrollStatus.ACTUAL,
                pension_plan_id=11,
            ),
            [22],
            ["PENSION_BASE", "INCOME_TAX"],
            "must have computed contributions and income tax before review",
        ),
    ],
)
async def test_sqlalchemy_payroll_repository_rejects_invalid_review_period_inputs(
    period: PayrollPeriodModel,
    assigned_plan_ids: list[int],
    present_codes: list[str],
    message: str,
) -> None:
    """Test sqlalchemy payroll repository rejects invalid review period inputs."""
    results = [
        FakeResult(scalar_one=period),
        FakeResult(scalar_rows=assigned_plan_ids),
    ]
    if period.pension_plan_id is not None and assigned_plan_ids:
        results.append(FakeResult(scalar_rows=present_codes))
    repository = SqlAlchemyPayrollRepository(FakeSession(results))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=message):
        await repository.review_period(SimpleNamespace(period_id=5))


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_saves_computed_contributions() -> None:
    """Test sqlalchemy payroll repository saves computed contributions."""
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
                    SimpleNamespace(id=5, code="UNEMPLOYMENT_INSURANCE"),
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
            unemployment=UnemploymentContribution(
                contract_kind=EmploymentContractKind.INDEFINITE,
                taxable_clp=Decimal("1000000"),
                cap_clp=Decimal("3000000"),
                capped_base_clp=Decimal("1000000"),
                employee_rate=Decimal("0.006"),
                employee_amount_clp=Decimal("6000"),
                employer_rate=Decimal("0.024"),
                employer_amount_clp=Decimal("24000"),
            ),
        )
    )

    assert result.period_id == 5
    assert period.pension_plan_id == 11
    assert sum(isinstance(item, PayrollItemModel) for item in session.added) == 5
    assert session.commit_count == 3
    assert any(
        "DELETE FROM payroll_items" in str(statement) for statement in session.executed
    )


@pytest.mark.asyncio
async def test_sa_payroll_repository_keeps_net_pay_pending_until_tax_exists() -> None:
    """Test net pay reconciliation stays pending after contributions only."""
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.PROJECTED,
        declared_net_pay_clp=Decimal("830000"),
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
                    SimpleNamespace(id=5, code="UNEMPLOYMENT_INSURANCE"),
                ]
            ),
            FakeResult(),
            FakeResult(),
            FakeResult(
                scalar_rows=[
                    "PENSION_BASE",
                    "PENSION_ADDITIONAL",
                    "HEALTH_BASE",
                    "HEALTH_ADDITIONAL_UF",
                    "UNEMPLOYMENT_INSURANCE",
                ]
            ),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    await repository.save_computed_contributions(
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
            unemployment=UnemploymentContribution(
                contract_kind=EmploymentContractKind.INDEFINITE,
                taxable_clp=Decimal("1000000"),
                cap_clp=Decimal("3000000"),
                capped_base_clp=Decimal("1000000"),
                employee_rate=Decimal("0.006"),
                employee_amount_clp=Decimal("6000"),
                employer_rate=Decimal("0.024"),
                employer_amount_clp=Decimal("24000"),
            ),
        )
    )

    assert period.expected_net_pay_clp is None
    assert period.net_pay_difference_clp is None
    assert session.commit_count == 3


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_missing_period_when_saving_contribs() -> (
    None
):
    """Test rejection when saving contributions for a missing period."""
    repository = SqlAlchemyPayrollRepository(FakeSession([FakeResult(scalar_one=None)]))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Payroll period 5 was not found."):
        await repository.save_computed_contributions(
            SimpleNamespace(
                period_id=5,
                pension_plan_id=1,
                health_plan_id=2,
                pension=object(),
                health=object(),
                unemployment=object(),
            )
        )


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_missing_concepts_when_saving() -> None:
    """Test rejection when contribution concepts are missing."""
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

    with pytest.raises(
        ValueError, match="Missing payroll concepts for computed contributions"
    ):
        await repository.save_computed_contributions(
            SimpleNamespace(
                period_id=5,
                pension_plan_id=1,
                health_plan_id=2,
                pension=object(),
                health=object(),
                unemployment=object(),
            )
        )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_returns_period_detail_and_summary() -> (
    None
):
    """Test sqlalchemy payroll repository returns period detail and summary."""
    period = PayrollPeriodModel(
        id=7,
        employer_id=1,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        worked_days=30,
        status=PayrollStatus.ACTUAL,
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
        pension_plan_id=1,
    )
    employer = EmployerModel(
        id=1,
        name="ACME",
        tax_id="76.123.456-7",
        country_code="CL",
        started_at=date(2020, 1, 1),
    )
    next_employer_started_at = date(2026, 2, 1)
    summary = PayrollSummaryModel(
        period_id=7,
        employer_id=1,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1000000"),
        total_discounts_clp=Decimal("170000"),
        net_pay_clp=Decimal("830000"),
    )
    session = FakeSession(
        [
            FakeResult(first_row=(period, employer)),
            FakeResult(scalar_one=next_employer_started_at),
            FakeResult(scalar_rows=[2, 3]),
            FakeResult(scalar_one=True),
            FakeResult(
                joined_rows=[
                    (
                        SimpleNamespace(amount_clp=Decimal("1000000"), notes=None),
                        SimpleNamespace(
                            code="SALARY_BASE",
                            name="Base Salary",
                            kind=SimpleNamespace(value="income"),
                            is_taxable=True,
                        ),
                    ),
                    (
                        SimpleNamespace(amount_clp=Decimal("100000"), notes="computed"),
                        SimpleNamespace(
                            code="PENSION_BASE",
                            name="Pension Base",
                            kind=SimpleNamespace(value="discount"),
                            is_taxable=False,
                        ),
                    ),
                ]
            ),
            FakeResult(first_row=(summary, employer)),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.get_period_detail(7)

    assert result is not None
    assert result.employer_name == "ACME"
    assert result.employment_contract_kind is EmploymentContractKind.INDEFINITE
    assert result.employer_started_at == date(2020, 1, 1)
    assert result.employer_ended_at == date(2026, 1, 31)
    assert result.health_institution_is_active is True
    assert result.health_plan_ids == (2, 3)
    assert result.items[0].concept_code == "SALARY_BASE"
    assert result.summary is not None
    assert result.summary.net_pay_clp == Decimal("830000")


@pytest.mark.asyncio
async def test_repository_returns_period_detail_without_end_date() -> None:
    """Test period detail keeps employer end date open without a later employer."""
    period = PayrollPeriodModel(
        id=7,
        employer_id=1,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        worked_days=30,
        status=PayrollStatus.ACTUAL,
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
    )
    employer = EmployerModel(
        id=1,
        name="ACME",
        tax_id="76.123.456-7",
        country_code="CL",
        started_at=date(2020, 1, 1),
    )
    session = FakeSession(
        [
            FakeResult(first_row=(period, employer)),
            FakeResult(scalar_one=None),
            FakeResult(scalar_rows=[]),
            FakeResult(joined_rows=[]),
            FakeResult(first_row=None),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.get_period_detail(7)

    assert result is not None
    assert result.employer_ended_at is None


@pytest.mark.asyncio
async def test_repository_returns_explicit_period_detail_end_date() -> None:
    """Test period detail uses the explicit employer end date when present."""
    period = PayrollPeriodModel(
        id=7,
        employer_id=1,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        worked_days=30,
        status=PayrollStatus.ACTUAL,
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
    )
    employer = EmployerModel(
        id=1,
        name="ACME",
        tax_id="76.123.456-7",
        country_code="CL",
        started_at=date(2020, 1, 1),
        ended_at=date(2026, 1, 15),
    )
    session = FakeSession(
        [
            FakeResult(first_row=(period, employer)),
            FakeResult(scalar_rows=[2]),
            FakeResult(scalar_one=False),
            FakeResult(joined_rows=[]),
            FakeResult(first_row=None),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.get_period_detail(7)

    assert result is not None
    assert result.employer_ended_at == date(2026, 1, 15)
    assert result.health_institution_is_active is False


@pytest.mark.asyncio
async def test_sa_payroll_repository_returns_none_for_missing_period_detail() -> None:
    """Test sqlalchemy payroll repository returns none for missing period detail."""
    repository = SqlAlchemyPayrollRepository(FakeSession([FakeResult(first_row=None)]))  # type: ignore[arg-type]

    assert await repository.get_period_detail(99) is None


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_lists_period_summaries() -> None:
    """Test sqlalchemy payroll repository lists period summaries."""
    employer = EmployerModel(id=1, name="ACME", started_at=date(2020, 1, 1))
    session = FakeSession(
        [
            FakeResult(
                joined_rows=[
                    (
                        PayrollSummaryModel(
                            period_id=7,
                            employer_id=1,
                            period_year=2026,
                            period_month=1,
                            payment_date=date(2026, 1, 31),
                            taxable_income_clp=Decimal("1000000"),
                            gross_income_clp=Decimal("1000000"),
                            total_discounts_clp=Decimal("170000"),
                            net_pay_clp=Decimal("830000"),
                        ),
                        employer,
                        PayrollPeriodModel(
                            id=7,
                            employer_id=1,
                            period_year=2026,
                            period_month=1,
                            payment_date=date(2026, 1, 31),
                            status=PayrollStatus.ACTUAL,
                            declared_net_pay_clp=Decimal("830000"),
                            expected_net_pay_clp=Decimal("830000"),
                            net_pay_difference_clp=Decimal("0"),
                        ),
                    )
                ]
            )
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.list_period_summaries()

    assert len(result) == 1
    assert result[0].period_id == 7
    assert result[0].employer_name == "ACME"
    assert result[0].net_pay_difference_clp == Decimal("0")


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_lists_period_ranges() -> None:
    """Test payroll period ranges use the latest paid payroll and employer rule."""
    current_period = PayrollPeriodModel(
        id=17,
        employer_id=1,
        period_year=2026,
        period_month=3,
        payment_date=date(2026, 3, 28),
        status=PayrollStatus.ACTUAL,
        declared_net_pay_clp=Decimal("2978086"),
    )
    current_employer = EmployerModel(
        id=1,
        name="WALMART-CHILE",
        country_code="CL",
        started_at=date(2024, 11, 18),
        payment_date_rule=EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH,
        payment_month_offset=0,
        payment_day_of_month=None,
        payment_business_day_offset=1,
        payment_calendar_day_offset=0,
        payment_effective_on_processing_next_day=True,
        payment_fixed_day_roll=EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY,
    )
    previous_period = PayrollPeriodModel(
        id=16,
        employer_id=1,
        period_year=2026,
        period_month=2,
        payment_date=date(2026, 2, 26),
        status=PayrollStatus.ACTUAL,
        declared_net_pay_clp=Decimal("2983237"),
    )
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_rows=[previous_period]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.list_period_ranges(today=date(2026, 3, 31))

    assert "declared_net_pay_clp IS NOT NULL" in str(session.executed[0])
    assert "payment_date <=" in str(session.executed[0])
    assert len(result) == 25
    assert result[11].period_year == 2026
    assert result[11].period_month == 2
    assert result[11].start_date == date(2026, 2, 26)
    assert result[11].end_date == date(2026, 3, 27)
    assert result[11].net_pay_clp == Decimal("2983237")
    assert result[11].inferred is False
    assert result[12].is_current is True
    assert result[12].start_date == date(2026, 3, 28)
    assert result[12].end_date == date(2026, 4, 28)
    assert result[12].net_pay_clp == Decimal("2978086")
    assert result[13].period_year == 2026
    assert result[13].period_month == 4
    assert result[13].start_date == date(2026, 4, 29)
    assert result[13].end_date == date(2026, 5, 27)
    assert result[20].period_year == 2026
    assert result[20].period_month == 11
    assert result[20].start_date == date(2026, 11, 27)
    assert result[0].inferred is True
    assert result[0].start_date == date(2025, 3, 31)


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_applies_effective_processing_dates() -> (
    None
):
    """Test inferred future periods can use effective processing-next-day dates."""
    current_period = PayrollPeriodModel(
        id=18,
        employer_id=2,
        period_year=2026,
        period_month=4,
        payment_date=date(2026, 4, 23),
        status=PayrollStatus.ACTUAL,
        declared_net_pay_clp=Decimal("2500000"),
    )
    current_employer = EmployerModel(
        id=2,
        name="CLINICA-ALEMANA",
        country_code="CL",
        started_at=date(2018, 4, 3),
        payment_date_rule=EmployerPaymentDateRule.CALENDAR_DAYS_BEFORE_END_OF_MONTH,
        payment_month_offset=0,
        payment_day_of_month=None,
        payment_business_day_offset=0,
        payment_calendar_day_offset=7,
        payment_effective_on_processing_next_day=True,
        payment_fixed_day_roll=EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY,
    )
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_rows=[]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.list_period_ranges(today=date(2026, 4, 30))

    assert result[13].period_year == 2026
    assert result[13].period_month == 5
    assert result[13].start_date == date(2026, 5, 23)
    assert result[13].end_date == date(2026, 6, 22)
    assert result[13].net_pay_clp is None
    assert result[13].inferred is True


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_infers_current_month_offset() -> None:
    """Test future ranges align to the observed current payment-month offset."""
    current_period = PayrollPeriodModel(
        id=19,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 5, 28),
        status=PayrollStatus.ACTUAL,
        declared_net_pay_clp=Decimal("3134978"),
    )
    current_employer = EmployerModel(
        id=1,
        name="WALMART-CHILE",
        country_code="CL",
        started_at=date(2024, 11, 18),
        payment_date_rule=EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH,
        payment_month_offset=0,
        payment_day_of_month=None,
        payment_business_day_offset=1,
        payment_calendar_day_offset=0,
        payment_effective_on_processing_next_day=True,
        payment_fixed_day_roll=EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY,
    )
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_rows=[]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.list_period_ranges(today=date(2026, 6, 11))

    assert result[12].period_year == 2026
    assert result[12].period_month == 6
    assert result[12].start_date == date(2026, 5, 28)
    assert result[12].end_date == date(2026, 6, 25)
    assert result[13].period_year == 2026
    assert result[13].period_month == 7
    assert result[13].start_date == date(2026, 6, 26)
    assert result[13].end_date == date(2026, 7, 29)


def test_sqlalchemy_payroll_repository_keeps_configured_offset_when_unmatched() -> None:
    """Test month-offset inference falls back to the configured offset."""
    repository = SqlAlchemyPayrollRepository(None)  # type: ignore[arg-type]

    resolved_offset = repository._resolve_effective_month_offset(
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 5, 27),
        country_code="CL",
        payment_date_rule=EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH.value,
        payment_month_offset=0,
        payment_day_of_month=None,
        payment_business_day_offset=1,
        payment_calendar_day_offset=0,
        payment_effective_on_processing_next_day=True,
        payment_fixed_day_roll=EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY.value,
    )

    assert resolved_offset == 0


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_lists_period_ranges_without_current() -> (
    None
):
    """Test payroll period ranges fall back to the current calendar month."""
    session = FakeSession(
        [
            FakeResult(first_row=None),
            FakeResult(scalar_rows=[]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.list_period_ranges(today=date(2026, 1, 15))

    assert len(result) == 25
    assert result[12].period_year == 2026
    assert result[12].period_month == 1
    assert result[12].start_date == date(2026, 1, 30)
    assert result[12].end_date == date(2026, 2, 26)
    assert result[12].net_pay_clp is None
    assert result[12].is_current is True
    assert result[12].inferred is True
    assert result[13].start_date == date(2026, 2, 27)


@pytest.mark.asyncio
async def test_sa_payroll_repository_builds_income_tax_context_and_bracket() -> None:
    """Test sqlalchemy payroll repository builds income tax context and bracket."""
    period = PayrollPeriodModel(
        id=5,
        employer_id=1,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.ACTUAL,
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(scalar_one=Decimal("1000000")),
            FakeResult(scalar_one=Decimal("176000")),
            FakeResult(
                scalar_one=IncomeTaxBracketModel(
                    id=1,
                    valid_from=date(2026, 1, 1),
                    valid_to=None,
                    lower_bound_utm=Decimal("0"),
                    upper_bound_utm=Decimal("13.5"),
                    marginal_rate=Decimal("0"),
                    rebate_utm=Decimal("0"),
                )
            ),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    context = await repository.get_income_tax_context(SimpleNamespace(period_id=5))
    bracket = await repository.get_income_tax_bracket(
        date(2026, 1, 31), Decimal("12.388060")
    )

    assert context.taxable_income_clp == Decimal("1000000")
    assert context.deductible_amount_clp == Decimal("176000")
    assert bracket == IncomeTaxBracket(
        valid_from=date(2026, 1, 1),
        valid_to=None,
        lower_bound_utm=Decimal("0"),
        upper_bound_utm=Decimal("13.5"),
        marginal_rate=Decimal("0"),
        rebate_utm=Decimal("0"),
    )


@pytest.mark.asyncio
async def test_income_tax_ctx_excludes_health_additional() -> None:
    """Test income-tax context excludes additional health plan charges."""
    period = PayrollPeriodModel(
        id=5,
        employer_id=1,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.ACTUAL,
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(scalar_one=Decimal("1000000")),
            FakeResult(scalar_one=Decimal("143101")),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    context = await repository.get_income_tax_context(SimpleNamespace(period_id=5))

    assert context.taxable_income_clp == Decimal("1000000")
    assert context.deductible_amount_clp == Decimal("143101")


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_missing_period_for_income_tax_ctx() -> (
    None
):
    """Test rejection when income-tax context has no matching period."""
    repository = SqlAlchemyPayrollRepository(FakeSession([FakeResult(scalar_one=None)]))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Payroll period 5 was not found."):
        await repository.get_income_tax_context(SimpleNamespace(period_id=5))


@pytest.mark.asyncio
async def test_sa_payroll_repository_returns_none_for_missing_income_tax_bracket() -> (
    None
):
    """Test returning None when no income-tax bracket matches."""
    repository = SqlAlchemyPayrollRepository(FakeSession([FakeResult(scalar_one=None)]))  # type: ignore[arg-type]

    assert (
        await repository.get_income_tax_bracket(date(2026, 1, 31), Decimal("20"))
        is None
    )


@pytest.mark.asyncio
async def test_sa_payroll_repository_builds_unemployment_context() -> None:
    """Test sqlalchemy payroll repository builds unemployment context."""
    period = PayrollPeriodModel(
        id=5,
        employer_id=1,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.ACTUAL,
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(
                scalar_one=ContributionCapModel(
                    id=1,
                    cap_type=ContributionCapType.UNEMPLOYMENT,
                    valid_from=date(2026, 1, 1),
                    valid_to=None,
                    value_uf=Decimal("122.6000"),
                )
            ),
            FakeResult(scalar_one=Decimal("1000000")),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    context = await repository.get_unemployment_context(SimpleNamespace(period_id=5))

    assert context.period_id == 5
    assert context.taxable_income_clp == Decimal("1000000")
    assert context.employment_contract_kind is EmploymentContractKind.INDEFINITE
    assert context.unemployment_cap.value_uf == Decimal("122.6000")


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_saves_computed_income_tax() -> None:
    """Test sqlalchemy payroll repository saves computed income tax."""
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
            FakeResult(scalar_one=SimpleNamespace(id=9, code="INCOME_TAX")),
            FakeResult(),
            FakeResult(),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.save_computed_income_tax(
        SimpleNamespace(
            period_id=5,
            tax=SimpleNamespace(tax_clp=Decimal("674")),
        )
    )

    assert result.period_id == 5
    assert sum(isinstance(item, PayrollItemModel) for item in session.added) == 1
    assert session.commit_count == 3


@pytest.mark.asyncio
async def test_sa_payroll_repository_saves_computed_unemployment() -> None:
    """Test sqlalchemy payroll repository saves computed unemployment."""
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
                scalar_one=SimpleNamespace(id=10, code="UNEMPLOYMENT_INSURANCE")
            ),
            FakeResult(),
            FakeResult(),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.save_computed_unemployment(
        SimpleNamespace(
            period_id=5,
            unemployment=SimpleNamespace(employee_amount_clp=Decimal("23716")),
        )
    )

    assert result.period_id == 5
    assert sum(isinstance(item, PayrollItemModel) for item in session.added) == 1
    assert session.commit_count == 3


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_missing_unemployment_concept() -> None:
    """Test rejection when unemployment concept is missing."""
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.PROJECTED,
    )
    repository = SqlAlchemyPayrollRepository(
        FakeSession([FakeResult(scalar_one=period), FakeResult(scalar_one=None)])
    )  # type: ignore[arg-type]

    with pytest.raises(
        ValueError,
        match="Missing payroll concept for computed contributions: "
        "UNEMPLOYMENT_INSURANCE",
    ):
        await repository.save_computed_unemployment(
            SimpleNamespace(
                period_id=5,
                unemployment=SimpleNamespace(employee_amount_clp=Decimal("23716")),
            )
        )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_reconciles_net_pay_after_tax() -> None:
    """Test net pay reconciliation completes after tax is saved."""
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.PROJECTED,
        declared_net_pay_clp=Decimal("830000"),
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(scalar_one=SimpleNamespace(id=9, code="INCOME_TAX")),
            FakeResult(),
            FakeResult(),
            FakeResult(
                scalar_rows=[
                    "PENSION_BASE",
                    "PENSION_ADDITIONAL",
                    "HEALTH_BASE",
                    "HEALTH_ADDITIONAL_UF",
                    "UNEMPLOYMENT_INSURANCE",
                    "INCOME_TAX",
                ]
            ),
            FakeResult(scalar_one=Decimal("830000")),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    await repository.save_computed_income_tax(
        SimpleNamespace(
            period_id=5,
            tax=SimpleNamespace(tax_clp=Decimal("674")),
        )
    )

    assert period.expected_net_pay_clp == Decimal("830000")
    assert period.net_pay_difference_clp == Decimal("0")
    assert session.commit_count == 3


@pytest.mark.asyncio
async def test_sa_payroll_repository_keeps_net_pay_pending_without_summary_row() -> (
    None
):
    """Test reconciliation stays pending when the summary row is unavailable."""
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.PROJECTED,
        declared_net_pay_clp=Decimal("830000"),
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(scalar_one=SimpleNamespace(id=9, code="INCOME_TAX")),
            FakeResult(),
            FakeResult(),
            FakeResult(
                scalar_rows=[
                    "PENSION_BASE",
                    "PENSION_ADDITIONAL",
                    "HEALTH_BASE",
                    "HEALTH_ADDITIONAL_UF",
                    "UNEMPLOYMENT_INSURANCE",
                    "INCOME_TAX",
                ]
            ),
            FakeResult(scalar_one=None),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    await repository.save_computed_income_tax(
        SimpleNamespace(
            period_id=5,
            tax=SimpleNamespace(tax_clp=Decimal("674")),
        )
    )

    assert period.expected_net_pay_clp is None
    assert period.net_pay_difference_clp is None
    assert session.commit_count == 3


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_missing_period_when_saving_tax() -> None:
    """Test rejection when saving income tax for a missing period."""
    repository = SqlAlchemyPayrollRepository(FakeSession([FakeResult(scalar_one=None)]))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Payroll period 5 was not found."):
        await repository.save_computed_income_tax(
            SimpleNamespace(period_id=5, tax=SimpleNamespace(tax_clp=Decimal("1")))
        )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_rejects_missing_income_tax_concept() -> (
    None
):
    """Test sqlalchemy payroll repository rejects missing income tax concept."""
    period = PayrollPeriodModel(
        id=5,
        employer_id=10,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        status=PayrollStatus.PROJECTED,
    )
    repository = SqlAlchemyPayrollRepository(
        FakeSession([FakeResult(scalar_one=period), FakeResult(scalar_one=None)])
    )  # type: ignore[arg-type]

    with pytest.raises(
        ValueError, match="Missing payroll concept for computed income tax: INCOME_TAX"
    ):
        await repository.save_computed_income_tax(
            SimpleNamespace(period_id=5, tax=SimpleNamespace(tax_clp=Decimal("1")))
        )


@pytest.mark.asyncio
async def test_api_dependencies_build_payroll_repository_and_use_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test api dependencies build payroll repository and use case."""
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

    repository = dependencies.get_payroll_repository(fake_session)  # type: ignore[arg-type]
    use_case = dependencies.get_import_payroll_use_case(repository)
    queries = dependencies.get_payroll_queries(repository)
    report_use_case = dependencies.get_generate_payroll_report_use_case(repository)
    assign_use_case = dependencies.get_assign_plans_use_case(repository)
    review_use_case = dependencies.get_review_payroll_period_use_case(repository)
    compute_use_case = dependencies.get_compute_contributions_use_case(repository)
    compute_tax_use_case = dependencies.get_compute_income_tax_use_case(
        repository, repository
    )  # type: ignore[arg-type]

    assert isinstance(repository, SqlAlchemyPayrollRepository)
    assert isinstance(use_case, ImportPayroll)
    assert isinstance(assign_use_case, AssignPlans)
    assert isinstance(report_use_case, GeneratePayrollReport)
    assert isinstance(review_use_case, ReviewPayrollPeriod)
    assert queries.__class__.__name__ == "PayrollQueries"
    assert compute_use_case.__class__.__name__ == "ComputeContributions"
    assert compute_tax_use_case.__class__.__name__ == "ComputeIncomeTax"


def test_payroll_models_are_declared() -> None:
    """Test payroll models are declared."""
    assert EmployerModel.__tablename__ == "employers"
    assert PayrollPeriodModel.__tablename__ == "payroll_periods"
    assert PayrollItemModel.__tablename__ == "payroll_items"
    assert PayrollSummaryModel.__tablename__ == "mv_payroll_summary"
    assert PayrollStatus.ACTUAL.value == "actual"
    assert EmploymentContractKind.INDEFINITE.value == "indefinite"


def test_get_last_day_of_month_for_various_months() -> None:
    """Test get_last_day_of_month returns correct last day for each month."""
    assert get_last_day_of_month(date(2026, 1, 15)) == date(2026, 1, 31)
    assert get_last_day_of_month(date(2026, 2, 15)) == date(2026, 2, 28)
    assert get_last_day_of_month(date(2026, 4, 15)) == date(2026, 4, 30)
    assert get_last_day_of_month(date(2026, 6, 15)) == date(2026, 6, 30)
    assert get_last_day_of_month(date(2024, 2, 15)) == date(2024, 2, 29)


def test_get_last_day_of_month_when_input_is_month_end() -> None:
    """Test get_last_day_of_month keeps month-end dates unchanged."""
    january_end = date(2026, 1, 31)
    february_end = date(2026, 2, 28)
    april_end = date(2026, 4, 30)
    leap_february_end = date(2024, 2, 29)

    assert get_last_day_of_month(january_end) == january_end
    assert get_last_day_of_month(february_end) == february_end
    assert get_last_day_of_month(april_end) == april_end
    assert get_last_day_of_month(leap_february_end) == leap_february_end


def test_build_fx_provider_uses_api_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test _build_fx_provider delegates to API dependency wiring."""
    fake_provider = object()
    monkeypatch.setattr(
        dependencies,
        "get_fx_rate_provider",
        lambda: fake_provider,
    )

    assert _build_fx_provider() is fake_provider


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_returns_none_for_missing_uf() -> None:
    """Test predict_next_period_net_pay returns None when UF data is missing."""
    current_period = PayrollPeriodModel(
        id=1,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 6, 26),
        status=PayrollStatus.ACTUAL,
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=None),  # uf_current month-end missing
            FakeResult(scalar_one=None),  # latest UF in DB missing
        ]
    )
    fx_provider = FakeFxRateProvider()

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1), fx_provider=fx_provider
    )

    assert result is None


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_returns_none_for_missing_income() -> None:
    """Test predict_next_period_net_pay returns None when income is missing."""
    current_period = PayrollPeriodModel(
        id=1,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 6, 26),
        status=PayrollStatus.ACTUAL,
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=Decimal("40821.18")),  # uf_current
            FakeResult(joined_rows=[]),  # items (empty)
        ]
    )
    fx_provider = FakeFxRateProvider()

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1), fx_provider=fx_provider
    )

    assert result is None


@pytest.mark.asyncio
async def test_predict_next_period_returns_none_missing_uf() -> None:
    """Test predict_next_period_net_pay returns None when selected UF is missing."""
    current_period = PayrollPeriodModel(
        id=1,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 6, 26),
        status=PayrollStatus.ACTUAL,
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=None),  # selected UF month-end missing
            FakeResult(scalar_one=None),  # latest UF in DB missing
        ]
    )
    fx_provider = FakeFxRateProvider()

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1), fx_provider=fx_provider
    )

    assert result is None


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_uf_fallback_cascade() -> None:
    """Test UF lookup cascade: provider target, provider today, then latest DB."""
    current_period = PayrollPeriodModel(
        id=1,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 6, 26),
        status=PayrollStatus.ACTUAL,
    )
    items = [(Decimal("1000"), "SALARY_BASE")]

    target_provider_session = FakeSession(
        [
            FakeResult(scalar_one=None),  # selected UF month-end missing
            FakeResult(joined_rows=items),
        ]
    )
    target_provider = FakeFxRateProvider({date(2026, 6, 30): Decimal("40820.31")})
    target_provider_result = await predict_next_period_net_pay(
        target_provider_session,
        current_period,
        date(2026, 6, 1),
        fx_provider=target_provider,
    )

    today_provider_session = FakeSession(
        [
            FakeResult(scalar_one=None),  # selected UF month-end missing
            FakeResult(joined_rows=items),
        ]
    )
    today_provider = FakeFxRateProvider({date.today(): Decimal("40820.31")})
    today_provider_result = await predict_next_period_net_pay(
        today_provider_session,
        current_period,
        date(2026, 6, 1),
        fx_provider=today_provider,
    )

    latest_db_session = FakeSession(
        [
            FakeResult(scalar_one=None),  # selected UF month-end missing
            FakeResult(scalar_one=Decimal("40820.31")),  # latest UF in DB
            FakeResult(joined_rows=items),
        ]
    )
    latest_db_result = await predict_next_period_net_pay(
        latest_db_session,
        current_period,
        date(2026, 6, 1),
        fx_provider=FakeFxRateProvider(),
    )

    assert target_provider_result == Decimal("1000.00")
    assert today_provider_result == Decimal("1000.00")
    assert latest_db_result == Decimal("1000.00")


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_db_only_mode() -> None:
    """Test prediction can resolve UF from DB only without provider lookups."""
    current_period = PayrollPeriodModel(
        id=1,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 6, 26),
        status=PayrollStatus.ACTUAL,
    )
    session = FakeSession(
        [
            FakeResult(scalar_one=None),  # selected UF month-end missing
            FakeResult(scalar_one=Decimal("40820.31")),  # latest UF in DB
            FakeResult(joined_rows=[(Decimal("1000"), "SALARY_BASE")]),
        ]
    )
    fx_provider = FakeFxRateProvider({date.today(): Decimal("99999.99")})

    result = await predict_next_period_net_pay(
        session,
        current_period,
        date(2026, 6, 1),
        fx_provider=fx_provider,
        allow_provider_lookup=False,
    )

    assert result == Decimal("1000.00")


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_calculates_correctly() -> None:
    """Test prediction recalculates UF-based discounts with selected UF."""
    current_period = PayrollPeriodModel(
        id=1,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 6, 26),
        status=PayrollStatus.ACTUAL,
        worked_days=30,
    )
    items = [
        (Decimal("3000000"), "SALARY_BASE"),
        (Decimal("200000"), "LEGAL_GRATUITY"),
        (Decimal("100000"), "TELEWORK_REFUND"),
        (Decimal("35000"), "HEALTH_ADDITIONAL_UF"),
        (Decimal("8000"), "HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION"),
        (Decimal("100000"), "PENSION_BASE"),
        (Decimal("50000"), "HEALTH_BASE"),
        (Decimal("30000"), "HEALTH_INSURANCE"),
        (Decimal("50000"), "INCOME_TAX"),
    ]
    selected_uf = Decimal("40821.18")
    current_reference_uf = Decimal("40000.00")
    session = FakeSession(
        [
            FakeResult(scalar_one=selected_uf),  # selected UF for prediction target
            FakeResult(joined_rows=items),  # items
            FakeResult(scalar_one=current_reference_uf),  # current-period UF reference
        ]
    )
    fx_provider = FakeFxRateProvider()

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1), fx_provider=fx_provider
    )

    employer_uf_quantity = Decimal("8000") / current_reference_uf
    future_employer_contribution = employer_uf_quantity * selected_uf
    future_health_additional_uf = (
        Decimal("35000") / current_reference_uf
    ) * selected_uf
    expected_gross = Decimal("3300000") + future_employer_contribution
    expected_discount_ratio = Decimal("230000") / Decimal("3308000")
    expected_discounts = (
        expected_gross * expected_discount_ratio
    ) + future_health_additional_uf
    expected_net_pay = expected_gross - expected_discounts

    assert result == expected_net_pay.quantize(Decimal("0.01"))


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_uses_historical_uf_fallback() -> None:
    """Test UF discounts fallback to latest historical UF when exact date is missing."""
    current_period = PayrollPeriodModel(
        id=1,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 6, 26),
        status=PayrollStatus.ACTUAL,
        worked_days=30,
    )
    items = [
        (Decimal("100000"), "SALARY_BASE"),
        (Decimal("10000"), "HEALTH_ADDITIONAL_UF"),
    ]
    session = FakeSession(
        [
            FakeResult(
                scalar_one=Decimal("41000.00")
            ),  # selected UF for prediction target
            FakeResult(joined_rows=items),  # items
            FakeResult(scalar_one=None),  # exact current-period UF missing
            FakeResult(scalar_one=Decimal("40000.00")),  # latest historical UF
        ]
    )

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1)
    )

    expected_uf_discount = (Decimal("10000") / Decimal("40000")) * Decimal("41000")
    expected_net_pay = Decimal("100000") - expected_uf_discount
    assert result == expected_net_pay.quantize(Decimal("0.01"))


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_returns_none_without_historical_uf() -> None:
    """Test UF-dependent prediction returns None when no historical UF is available."""
    current_period = PayrollPeriodModel(
        id=1,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 6, 26),
        status=PayrollStatus.ACTUAL,
        worked_days=30,
    )
    items = [
        (Decimal("100000"), "SALARY_BASE"),
        (Decimal("10000"), "HEALTH_ADDITIONAL_UF"),
    ]
    session = FakeSession(
        [
            FakeResult(
                scalar_one=Decimal("41000.00")
            ),  # selected UF for prediction target
            FakeResult(joined_rows=items),  # items
            FakeResult(scalar_one=None),  # exact current-period UF missing
            FakeResult(scalar_one=None),  # latest historical UF missing
        ]
    )

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1)
    )

    assert result is None


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_adjusts_for_worked_days() -> None:
    """Test predict adjusts income to 30 days if fewer days worked."""
    current_period = PayrollPeriodModel(
        id=1,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 6, 26),
        status=PayrollStatus.ACTUAL,
        worked_days=20,
    )
    items = [
        (Decimal("2000000"), "SALARY_BASE"),
        (Decimal("100000"), "LEGAL_GRATUITY"),
        (Decimal("50000"), "TELEWORK_REFUND"),
        (Decimal("70000"), "PENSION_BASE"),
        (Decimal("30000"), "HEALTH_BASE"),
        (Decimal("20000"), "HEALTH_INSURANCE"),
        (Decimal("35000"), "INCOME_TAX"),
    ]
    session = FakeSession(
        [
            FakeResult(scalar_one=Decimal("40821.18")),  # uf_current
            FakeResult(joined_rows=items),  # items
        ]
    )
    fx_provider = FakeFxRateProvider()

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1), fx_provider=fx_provider
    )

    # Income for 20 days
    income_20_days = Decimal("2150000")
    discounts_20_days = Decimal("155000")

    # Project to 30 days
    income_30_days = income_20_days * Decimal(30) / Decimal(20)
    discount_ratio = discounts_20_days / income_20_days
    predicted_discounts = income_30_days * discount_ratio
    expected_net_pay = income_30_days - predicted_discounts

    assert result == expected_net_pay.quantize(Decimal("0.01"))


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_returns_none_zero_net_pay() -> None:
    """Test predict_next_period_net_pay returns None when result would be <= 0."""
    current_period = PayrollPeriodModel(
        id=1,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 6, 26),
        status=PayrollStatus.ACTUAL,
    )
    items = [
        (Decimal("100"), "SALARY_BASE"),
        (Decimal("100"), "PENSION_BASE"),
        (Decimal("100"), "INCOME_TAX"),
    ]
    session = FakeSession(
        [
            FakeResult(scalar_one=Decimal("40821.18")),  # uf_current
            FakeResult(joined_rows=items),  # items
        ]
    )
    fx_provider = FakeFxRateProvider()

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1), fx_provider=fx_provider
    )

    assert result is None
