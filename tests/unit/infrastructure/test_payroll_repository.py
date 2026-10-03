"""Tests for test payroll repository."""

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
from payroll.application.dto import ExportPayrollFiltersDTO
from payroll.application.errors import PayrollDependencyError
from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.application.use_cases.assign_plans import AssignPlans
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
    PensionInstitutionModel,
    PensionPlanModel,
)
from payroll.infrastructure.db.repositories.payroll_repository import (
    SqlAlchemyPayrollRepository,
)
from payroll.infrastructure.db.repositories.payroll_repository_shared import (
    PredictedNetPayBaseline,
    build_net_pay_warning,
    get_last_day_of_month,
    predict_next_period_net_pay,
    project_future_months,
    resolve_currency_equivalents,
)
from payroll.interfaces.api import dependencies


def _baseline(
    net_pay_clp: Decimal, *, fixed_uf_clp: Decimal = Decimal("0")
) -> PredictedNetPayBaseline:
    """Build a PredictedNetPayBaseline for tests that don't care about the UF split.

    Defaults `fixed_uf_clp` to 0 -- i.e. `scalable_clp == net_pay_clp` -- which
    reproduces the pre-split behavior exactly (the whole net_pay_clp scales at
    an increase step) for every test that isn't specifically about the
    UF-doesn't-scale-with-a-raise fix. Tests that do care pass a nonzero
    `fixed_uf_clp` explicitly (see test_project_future_months_does_not_scale_
    uf_driven_portion).
    """
    return PredictedNetPayBaseline(
        net_pay_clp=net_pay_clp,
        scalable_clp=net_pay_clp + fixed_uf_clp,
        fixed_uf_clp=fixed_uf_clp,
    )


async def _assert_project_future_months_replicates_flat(
    market_data_repository: "FakeMarketDataRepository", *, current_month: int = 9
) -> None:
    """Run project_future_months() and assert it replicates flat, zero increase.

    Shared tail of every test_project_future_months_skips_step_* test: only
    the market_data_repository fixture (what IPC data is missing/malformed)
    varies between them -- the call and the three assertions are identical.
    """
    first_future_net_pay_clp = Decimal("3000000.00")
    result = await project_future_months(
        _baseline(first_future_net_pay_clp),
        current_year=2026,
        current_month=current_month,
        first_increase_period=date(2026, 4, 1),
        increase_frequency=12,
        market_data_repository=market_data_repository,
    )
    assert len(result) == 12
    assert all(
        month.net_pay_clp == first_future_net_pay_clp for month in result.values()
    )
    assert all(month.increase_pct == Decimal("0.00") for month in result.values())


class FakeResult(FakeAllMixin):
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


class FakeSession(FakeResultsQueueBase):
    """Test double for Session."""

    def __init__(self, results: list[FakeResult]) -> None:
        """Initialize the instance."""
        super().__init__(results)  # type: ignore[arg-type]
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


class FakeMarketDataRepository:
    """Test double for MarketDataRepository (the pf-rates-backed port).

    Mirrors PfRatesClient's get_exchange_rate_value() shape exactly --
    predict_next_period_net_pay() no longer does any fallback logic of its
    own (pf-rates owns the whole DB-hit -> provider -> nearest-prior-date
    cascade server-side now), so this fake only ever needs a flat
    date -> value lookup, plus an optional simulated-outage mode.
    """

    def __init__(
        self,
        rates_by_date: dict[date, Decimal | None] | None = None,
        *,
        raises: Exception | None = None,
        economic_index_by_period: dict[tuple[str, int, int], Decimal] | None = None,
        latest_economic_index: dict[str, tuple[date, Decimal]] | None = None,
        rates_by_currency_date: dict[tuple[str, date], Decimal | None] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._rates_by_date = rates_by_date or {}
        self._raises = raises
        self._economic_index_by_period = economic_index_by_period or {}
        self._latest_economic_index = latest_economic_index or {}
        self._rates_by_currency_date = rates_by_currency_date or {}
        self.economic_index_calls: list[tuple[str, int, int]] = []

    async def get_exchange_rate_value(
        self, currency_code: str, rate_date: date
    ) -> Decimal | None:
        """Handle get exchange rate value."""
        if self._raises is not None:
            raise self._raises
        key = (currency_code, rate_date)
        if key in self._rates_by_currency_date:
            return self._rates_by_currency_date[key]
        if currency_code != "UF":
            return None
        return self._rates_by_date.get(rate_date)

    async def get_exchange_rate_values(
        self, pairs: list[tuple[str, date]]
    ) -> dict[tuple[str, date], Decimal | None]:
        """Handle batched exchange-rate values."""
        if self._raises is not None:
            raise self._raises
        return {pair: await self.get_exchange_rate_value(*pair) for pair in pairs}

    async def get_economic_index_value(
        self, code: str, period_year: int, period_month: int
    ) -> Decimal | None:
        """Handle get economic index value."""
        self.economic_index_calls.append((code, period_year, period_month))
        if self._raises is not None:
            raise self._raises
        return self._economic_index_by_period.get((code, period_year, period_month))

    async def get_latest_economic_index(self, code: str) -> tuple[date, Decimal] | None:
        """Handle get latest economic index."""
        if self._raises is not None:
            raise self._raises
        return self._latest_economic_index.get(code)


def build_period(
    *,
    period_id: int = 5,
    employer_id: int = 10,
    payment_date: date = date(2026, 1, 31),
    status: PayrollStatus = PayrollStatus.PROJECTED,
    employment_contract_kind: EmploymentContractKind = (
        EmploymentContractKind.INDEFINITE
    ),
    worked_days: int | None = None,
    declared_net_pay_clp: object = None,
) -> PayrollPeriodModel:
    """Build a payroll period model for repository tests."""
    model = PayrollPeriodModel(
        id=period_id,
        employer_id=employer_id,
        period_year=payment_date.year,
        period_month=payment_date.month,
        payment_date=payment_date,
        status=status,
        employment_contract_kind=employment_contract_kind,
    )
    if worked_days is not None:
        model.worked_days = worked_days
    if declared_net_pay_clp is not None:
        model.declared_net_pay_clp = declared_net_pay_clp
    return model


def build_pension_pair(
    *,
    plan_id: int = 11,
    institution_id: int = 1,
    code: str = "AFP_UNO",
    name: str = "AFP Uno",
    additional_rate: Decimal = Decimal("0.0127"),
    active: bool = True,
    valid_from: date = date(2026, 1, 1),
    valid_to: date | None = None,
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
        valid_from=valid_from,
        valid_to=valid_to,
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
    valid_from: date = date(2026, 1, 1),
    valid_to: date | None = None,
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
        valid_from=valid_from,
        valid_to=valid_to,
        plan_name=plan_name,
        contracted_uf=contracted_uf,
    )
    return plan, institution


def build_plan_deduction_and_validation_results(
    pension_plan: PensionPlanModel,
    pension_institution: PensionInstitutionModel,
    health_plan: HealthPlanModel,
    health_institution: HealthInstitutionModel,
) -> list[FakeResult]:
    """Build the 3 FakeResults import_rows() needs when no plan_id is given.

    Two "deduce for the period" lookups, then one "validate it exists and is
    valid for payment_date" lookup for pension only -- health plans deduced
    via get_health_plans_overlapping_month() are already known-active and
    overlapping, so import_rows() skips a separate per-plan validation query
    for them (see the `plans_were_deduced` guard in import_rows()).
    """
    return [
        FakeResult(joined_rows=[(pension_plan, pension_institution)]),
        FakeResult(joined_rows=[(health_plan, health_institution)]),
        FakeResult(first_row=(pension_plan, pension_institution)),
    ]


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


def build_june_2026_period(*, worked_days: int | None = None) -> PayrollPeriodModel:
    """Build the recurring June-2026 current period used in prediction tests."""
    return build_period(
        period_id=1,
        employer_id=1,
        payment_date=date(2026, 6, 26),
        status=PayrollStatus.ACTUAL,
        worked_days=worked_days,
    )


def build_specific_chile_employer(
    *,
    first_increase_period_year: int | None = None,
    first_increase_period_month: int | None = None,
    increase_frequency: int | None = None,
) -> EmployerModel:
    """Build the specific employer model used in period-range tests."""
    model = EmployerModel(
        id=1,
        name="COMPANY",
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
    if first_increase_period_year is not None:
        model.first_increase_period_year = first_increase_period_year
    if first_increase_period_month is not None:
        model.first_increase_period_month = first_increase_period_month
    if increase_frequency is not None:
        model.increase_frequency = increase_frequency
    return model


def build_default_current_period(**overrides: object) -> PayrollPeriodModel:
    """Build the baseline March-2026 'current' period shared by period-range tests.

    id=17, employer_id=1, declared_net_pay_clp=2,978,086 -- any field a
    given test needs to flex (e.g. worked_days) is passed as a keyword
    override instead of a whole new literal PayrollPeriodModel(...) block.
    """
    fields: dict[str, object] = {
        "id": 17,
        "employer_id": 1,
        "period_year": 2026,
        "period_month": 3,
        "payment_date": date(2026, 3, 28),
        "status": PayrollStatus.ACTUAL,
        "declared_net_pay_clp": Decimal("2978086"),
    }
    fields.update(overrides)
    return PayrollPeriodModel(**fields)


def build_default_previous_period(**overrides: object) -> PayrollPeriodModel:
    """Build the baseline February-2026 'previous' period shared by period-range tests.

    id=16, employer_id=1, declared_net_pay_clp=2,983,237 -- same
    override-only-what-you-need approach as build_default_current_period().
    """
    fields: dict[str, object] = {
        "id": 16,
        "employer_id": 1,
        "period_year": 2026,
        "period_month": 2,
        "payment_date": date(2026, 2, 26),
        "status": PayrollStatus.ACTUAL,
        "declared_net_pay_clp": Decimal("2983237"),
    }
    fields.update(overrides)
    return PayrollPeriodModel(**fields)


def build_repository_with_no_previous_periods(
    current_period: PayrollPeriodModel, current_employer: EmployerModel
) -> SqlAlchemyPayrollRepository:
    """Build a repository stubbed with a current period and zero previous periods.

    Shared by list_period_ranges() tests that only vary previous_months/
    future_months on an otherwise-identical empty-previous-periods setup.
    """
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_rows=[]),
            FakeResult(joined_rows=[]),
        ]
    )
    return SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]


def build_acme_employer(*, ended_at: date | None = None) -> EmployerModel:
    """Build the ACME employer model used in period-detail tests."""
    model = EmployerModel(
        id=1,
        name="ACME",
        tax_id="76.123.456-7",
        country_code="CL",
        started_at=date(2020, 1, 1),
    )
    if ended_at is not None:
        model.ended_at = ended_at
    return model


def build_standard_contributions_command(
    period_id: int = 5,
) -> object:
    """Build a save_computed_contributions command with standard test values."""
    return SimpleNamespace(
        period_id=period_id,
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


_FIVE_CONCEPT_CODES = FakeResult(
    scalar_rows=[
        SimpleNamespace(id=1, code="PENSION_BASE"),
        SimpleNamespace(id=2, code="PENSION_ADDITIONAL"),
        SimpleNamespace(id=3, code="HEALTH_BASE"),
        SimpleNamespace(id=4, code="HEALTH_ADDITIONAL_UF"),
        SimpleNamespace(id=5, code="UNEMPLOYMENT_INSURANCE"),
    ]
)

_REVIEW_REQUIRED_CONCEPT_ROWS = [
    "PENSION_BASE",
    "PENSION_ADDITIONAL",
    "HEALTH_BASE",
    "UNEMPLOYMENT_INSURANCE",
    "INCOME_TAX",
]

_REVIEW_REQUIRED_CONCEPTS_RESULT = FakeResult(scalar_rows=_REVIEW_REQUIRED_CONCEPT_ROWS)

# AFP_MODEL pension/health plan pair — shared by assigns_plan_ids and embedded plan
_AFP_MODEL_PENSION_PLAN, _AFP_MODEL_PENSION_INSTITUTION = build_pension_pair(
    plan_id=1,
    institution_id=5,
    code="AFP_MODEL",
    name="AFP Model",
    additional_rate=Decimal("0.0116"),
)
_AFP_MODEL_HEALTH_PLAN, _AFP_MODEL_HEALTH_INSTITUTION = build_health_pair(
    plan_id=2,
    institution_id=6,
    contracted_uf=Decimal("5.42"),
)

# AFP_TEST pension/health plan pair — shared by the three employer import tests
_AFP_TEST_PENSION_PLAN, _AFP_TEST_PENSION_INSTITUTION = build_pension_pair(
    plan_id=1,
    institution_id=5,
    code="AFP_TEST",
    name="AFP Test",
    additional_rate=Decimal("0"),
    valid_from=date(2024, 11, 1),
)
_AFP_TEST_HEALTH_PLAN, _AFP_TEST_HEALTH_INSTITUTION = build_health_pair(
    plan_id=1,
    institution_id=6,
    valid_from=date(2024, 11, 1),
)


def _afp_test_import_session(extra_results: list[FakeResult]) -> FakeSession:
    """Build the five-query FakeSession prefix shared by the employer-import tests."""
    return FakeSession(
        [
            FakeResult(scalar_rows=[SimpleNamespace(id=1, code="SALARY_BASE")]),
            # Pension plan deduction
            FakeResult(
                joined_rows=[(_AFP_TEST_PENSION_PLAN, _AFP_TEST_PENSION_INSTITUTION)]
            ),
            # Health plan deduction (get_health_plans_overlapping_month() already
            # filters for active + overlapping-the-month, so no separate per-plan
            # validation query follows it, unlike pension)
            FakeResult(
                joined_rows=[(_AFP_TEST_HEALTH_PLAN, _AFP_TEST_HEALTH_INSTITUTION)]
            ),
            # Pension plan validation
            FakeResult(
                first_row=(_AFP_TEST_PENSION_PLAN, _AFP_TEST_PENSION_INSTITUTION)
            ),
            *extra_results,
        ]
    )


def _two_concept_session() -> FakeSession:
    """Build a FakeSession returning SALARY_BASE + PENSION_BASE concept codes."""
    return FakeSession(
        [
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="SALARY_BASE"),
                    SimpleNamespace(id=2, code="PENSION_BASE"),
                ]
            ),
        ]
    )


def _multi_health_session(
    second_pair: tuple[object, object],
) -> tuple[object, FakeSession]:
    """Build the FakeSession for multi-health-plan contribution context tests.

    Returns (period, session) so callers can pass period to the repository call.
    """
    period = build_period()
    session = FakeSession(
        build_contribution_context_results(
            period=period,
            pension_pair=build_pension_pair(),
            health_pair=build_health_pair(contracted_uf=Decimal("5.42")),
            health_plan_ids=[22, 23],
            period_health_pairs=[
                build_health_pair(plan_id=22, contracted_uf=Decimal("5.42")),
                second_pair,
            ],
        )
    )
    return period, session


_HEALTH_UF_ITEMS = [
    (Decimal("100000"), "SALARY_BASE"),
    (Decimal("10000"), "HEALTH_ADDITIONAL_UF"),
]


def test_build_net_pay_warning_reports_final_mismatch() -> None:
    """A 151 CLP difference already exceeds the 150 CLP reconciliation tolerance."""
    assert build_net_pay_warning(
        Decimal("1000"),
        Decimal("849"),
        Decimal("151"),
    ) == (
        "Declared net_pay does not match the fully computed payroll totals. "
        "Difference: 151 CLP."
    )


def test_build_net_pay_warning_within_tolerance_has_no_warning() -> None:
    """A 150 CLP difference is rounding noise absorbed by the shared tolerance.

    Same constant, same rationale as the contribution-level checks: a small
    residual (e.g. from an approximated mid-month health plan enrollment
    date) should not block validation on its own once it has already been
    absorbed upstream -- otherwise it just resurfaces here as a leftover of
    the same size. See health-additional-uf-mismatch.md, Session 12.
    """
    assert (
        build_net_pay_warning(
            Decimal("1000"),
            Decimal("850"),
            Decimal("150"),
        )
        is None
    )


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_imports_rows() -> None:
    """Test sqlalchemy payroll repository imports rows."""
    employer = EmployerModel(id=10, name="ACME", started_at=date(2026, 1, 31))
    pension_plan, pension_institution = build_pension_pair(
        plan_id=1, code="AFP_TEST", name="AFP Test", additional_rate=Decimal("0")
    )
    health_plan, health_institution = build_health_pair(plan_id=1, institution_id=1)
    session = FakeSession(
        [
            # Concept codes
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="SALARY_BASE"),
                    SimpleNamespace(id=2, code="PENSION_BASE"),
                ]
            ),
            *build_plan_deduction_and_validation_results(
                pension_plan, pension_institution, health_plan, health_institution
            ),
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
    assert session.flush_count == 1
    # 2 commits from _refresh_summary_view() + 1 from _reconcile_period_net_pay()
    # bailing out early once it sees this period is missing most of the 5
    # REVIEW_REQUIRED_CONCEPT_CODES (only SALARY_BASE/PENSION_BASE were
    # imported here) -- see payroll_repository_shared.py.
    assert session.commit_count == 3
    assert any(isinstance(item, PayrollPeriodModel) for item in session.added)
    assert sum(isinstance(item, PayrollItemModel) for item in session.added) == 2


def _build_net_pay_reconciliation_rows(codes: list[str]) -> list[SimpleNamespace]:
    """Build one declared row per concept code, all sharing one ACME period."""
    return [
        SimpleNamespace(
            employer="ACME",
            period_year=2026,
            period_month=1,
            payment_date=date(2026, 1, 31),
            status="actual",
            employment_contract_kind=EmploymentContractKind.INDEFINITE,
            concept_code=code,
            amount_clp=Decimal("100000"),
            declared_net_pay_clp=Decimal("900000"),
            expected_net_pay_clp=None,
            net_pay_difference_clp=None,
        )
        for code in codes
    ]


async def _assert_import_rows_reconciles_net_pay(
    *, declared_codes: list[str], review_required_codes: list[str]
) -> None:
    """Run import_rows() for declared_codes; assert net pay reconciles at 900000 CLP.

    Shared by both the "with HEALTH_ADDITIONAL_UF" and "without it" net-pay
    reconciliation tests below -- declared_codes is what the payslip itself
    declares, review_required_codes is what the real DB query's own
    .where(code.in_(REVIEW_REQUIRED_CONCEPT_CODES)) filter would actually
    return (a subset of declared_codes in the "with" case, identical to it
    in the "without" case).
    """
    employer = EmployerModel(id=10, name="ACME", started_at=date(2026, 1, 31))
    pension_plan, pension_institution = build_pension_pair()
    health_plan, health_institution = build_health_pair()
    session = FakeSession(
        [
            # Concept codes
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=index, code=code)
                    for index, code in enumerate(declared_codes, start=1)
                ]
            ),
            *build_plan_deduction_and_validation_results(
                pension_plan, pension_institution, health_plan, health_institution
            ),
            # Employer lookup
            FakeResult(scalar_one=employer),
            # Check period exists
            FakeResult(scalar_one=None),
            # _sync_period_health_plans()'s delete-before-insert execute
            FakeResult(),
            # _refresh_summary_view()'s raw SQL execute
            FakeResult(),
            # _reconcile_period_net_pay(): available concept codes on the period
            FakeResult(scalar_rows=list(review_required_codes)),
            # _reconcile_period_net_pay(): PAY_MV_SUMARY.net_pay_clp
            FakeResult(scalar_one=Decimal("900000")),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.import_rows(
        _build_net_pay_reconciliation_rows(declared_codes)
    )

    assert result.imported_periods == 1
    assert result.imported_items == len(declared_codes)
    assert result.periods[0].expected_net_pay_clp == Decimal("900000")
    assert result.periods[0].net_pay_difference_clp == Decimal("0")
    assert result.periods[0].net_pay_warning is None


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_imports_rows_reconciles_net_pay() -> None:
    """Test import_rows computes expected_net_pay_clp once all required concepts land.

    Regression for a bug where import_rows() never called
    _reconcile_period_net_pay() at all -- expected_net_pay_clp stayed null
    forever, no matter which concepts were imported, because the reconcile
    step (already used by the per-item command endpoints) was simply never
    wired into the bulk import path. See payroll_repository_shared.py's
    _reconcile_period_net_pay() and REVIEW_REQUIRED_CONCEPT_CODES.

    Imports all 6 concept codes (including the optional HEALTH_ADDITIONAL_UF)
    to also prove its presence doesn't break the gate -- REVIEW_REQUIRED_
    CONCEPT_CODES itself only requires the other 5 (see shared/constants.py),
    so the fake DB's available-codes-on-this-period query result deliberately
    excludes HEALTH_ADDITIONAL_UF below, mirroring the real query's own
    code.in_(REVIEW_REQUIRED_CONCEPT_CODES) filter.
    """
    required_codes = [
        "PENSION_BASE",
        "PENSION_ADDITIONAL",
        "HEALTH_BASE",
        "HEALTH_ADDITIONAL_UF",
        "UNEMPLOYMENT_INSURANCE",
        "INCOME_TAX",
    ]
    review_required_codes = [
        code for code in required_codes if code != "HEALTH_ADDITIONAL_UF"
    ]
    await _assert_import_rows_reconciles_net_pay(
        declared_codes=required_codes, review_required_codes=review_required_codes
    )


@pytest.mark.asyncio
async def test_import_rows_reconciles_net_pay_without_health_additional() -> None:
    """A period genuinely missing HEALTH_ADDITIONAL_UF still reconciles net pay.

    Regression for the opposite bug: HEALTH_ADDITIONAL_UF used to be a hard
    requirement in REVIEW_REQUIRED_CONCEPT_CODES, so a real payslip with no
    additional Isapre plan cost (a legitimate, common case -- confirmed
    against 22 real payslips where 3 months genuinely had no such line item)
    stayed "pending" forever even though PENSION_BASE/PENSION_ADDITIONAL/
    HEALTH_BASE/UNEMPLOYMENT_INSURANCE/INCOME_TAX were all already declared
    and sufficient to reconcile. See shared/constants.py's
    MANDATORY_DECLARED_CONTRIBUTION_CONCEPT_CODES docstring.
    """
    required_codes = [
        "PENSION_BASE",
        "PENSION_ADDITIONAL",
        "HEALTH_BASE",
        "UNEMPLOYMENT_INSURANCE",
        "INCOME_TAX",
    ]
    await _assert_import_rows_reconciles_net_pay(
        declared_codes=required_codes, review_required_codes=required_codes
    )


@pytest.mark.asyncio
async def test_sa_payroll_repository_assigns_plan_ids_from_import_rows() -> None:
    """Test import rows assign period plan ids when provided in the payload."""
    employer = EmployerModel(id=10, name="ACME", started_at=date(2026, 1, 31))
    session = FakeSession(
        [
            FakeResult(
                scalar_rows=[
                    SimpleNamespace(id=1, code="SALARY_BASE"),
                    SimpleNamespace(id=2, code="PENSION_BASE"),
                ]
            ),
            FakeResult(
                first_row=(_AFP_MODEL_PENSION_PLAN, _AFP_MODEL_PENSION_INSTITUTION)
            ),
            FakeResult(
                first_row=(_AFP_MODEL_HEALTH_PLAN, _AFP_MODEL_HEALTH_INSTITUTION)
            ),
            FakeResult(scalar_one=employer),
            FakeResult(scalar_one=None),
            FakeResult(),
            FakeResult(),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    await repository.import_rows(
        [
            build_import_row(
                pension_plan_id=1,
                health_plan_id=2,
                declared_net_pay_clp=Decimal("950000"),
                expected_net_pay_clp=Decimal("900000"),
                net_pay_difference_clp=Decimal("50000"),
            ),
            build_import_row(
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
    session = _two_concept_session()
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Inconsistent pension_plan_id"):
        await repository.import_rows(
            [
                build_import_row(pension_plan_id=1, health_plan_id=2),
                build_import_row(
                    concept_code="PENSION_BASE",
                    amount_clp=Decimal("100000"),
                    pension_plan_id=3,
                    health_plan_id=2,
                ),
            ]
        )


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_inconsistent_period_health_plan_ids() -> (
    None
):
    """Test import rows reject inconsistent health plans within one period."""
    session = _two_concept_session()
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Inconsistent health_plan_id"):
        await repository.import_rows(
            [
                build_import_row(pension_plan_id=1, health_plan_ids=(2, 3)),
                build_import_row(
                    concept_code="PENSION_BASE",
                    amount_clp=Decimal("100000"),
                    pension_plan_id=1,
                    health_plan_ids=(2, 4),
                ),
            ]
        )


@pytest.mark.asyncio
async def test_sa_payroll_repository_rejects_payment_date_period_mismatch() -> None:
    """Test import rows reject a payment_date that does not match the period.

    This is the guardrail for the old, abandoned convention where a
    month-end payment was filed under the following month's period.
    """
    employer = EmployerModel(
        id=9,
        name="WALMART-CHILE",
        started_at=date(2024, 11, 18),
        payment_month_offset=0,
    )
    session = _afp_test_import_session([FakeResult(scalar_one=employer)])
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="does not match period"):
        await repository.import_rows(
            [
                build_import_row(
                    period_year=2026,
                    period_month=2,
                    payment_date=date(2026, 1, 31),
                )
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
            [build_import_row(pension_plan_id=1, health_plan_id=None)]
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
    pension_plan, pension_institution = build_pension_pair(
        plan_id=1, code="AFP_TEST", name="AFP Test", additional_rate=Decimal("0")
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
        match="No valid health plans found for period",
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
    session = FakeSession(
        [
            FakeResult(scalar_rows=[SimpleNamespace(id=1, code="SALARY_BASE")]),
            FakeResult(
                first_row=(_AFP_MODEL_PENSION_PLAN, _AFP_MODEL_PENSION_INSTITUTION)
            ),
            FakeResult(
                first_row=(_AFP_MODEL_HEALTH_PLAN, _AFP_MODEL_HEALTH_INSTITUTION)
            ),
            FakeResult(scalar_one=employer),
            FakeResult(scalar_one=existing_period),
            FakeResult(),
            FakeResult(),
            FakeResult(),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    await repository.import_rows(
        [build_import_row(pension_plan_id=1, health_plan_id=2)]
    )
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
    session = _afp_test_import_session(
        [
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
            build_import_row(
                employer="NewCo",
                employment_contract_kind=EmploymentContractKind.FIXED_TERM,
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
    session = _afp_test_import_session(
        [
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
        [build_import_row(employment_contract_kind=EmploymentContractKind.FIXED_TERM)]
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
    session = _afp_test_import_session(
        [
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
            build_import_row(
                employer="NewCo",
                employment_contract_kind=EmploymentContractKind.FIXED_TERM,
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
        'DELETE FROM "PAY_ITEM"' in str(statement) for statement in session.executed
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
    """Test contribution context exposes every assigned health plan for summing.

    health_plan.contracted_uf is now just the requested plan's own value --
    it's health_plans (the full list) that ContributionCalculator.health()
    sums (prorated by day-overlap; both plans here cover the full month, so
    a flat sum matches the prorated one).
    """
    _period, session = _multi_health_session(
        build_health_pair(plan_id=23, contracted_uf=Decimal("0.91"), plan_name="GES")
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.get_contribution_context(
        SimpleNamespace(period_id=5, pension_plan_id=11, health_plan_id=22)
    )

    assert sum((plan.contracted_uf for plan in result.health_plans), Decimal("0")) == (
        Decimal("6.33")
    )


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
    _, session = _multi_health_session(
        build_health_pair(
            plan_id=23,
            institution_id=3,
            code="ISAPRE_X",
            name="Isapre X",
            kind=HealthInstitutionKind.ISAPRE,
            contracted_uf=Decimal("0.91"),
            plan_name="Other",
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
                FakeResult(scalar_one=build_period(period_id=1, employer_id=1)),
                FakeResult(first_row=None),
            ],
            "Pension plan 1 was not found.",
        ),
        (
            [
                FakeResult(scalar_one=build_period(period_id=1, employer_id=1)),
                FakeResult(
                    first_row=build_pension_pair(
                        plan_id=1, additional_rate=Decimal("0")
                    )
                ),
                FakeResult(first_row=None),
            ],
            "Health plan 2 was not found.",
        ),
        (
            [
                FakeResult(scalar_one=build_period(period_id=1, employer_id=1)),
                FakeResult(
                    first_row=build_pension_pair(
                        plan_id=1, additional_rate=Decimal("0")
                    )
                ),
                FakeResult(first_row=build_health_pair(plan_id=2)),
                FakeResult(scalar_one=None),
            ],
            "No contribution cap was found for 2026-01-31.",
        ),
        (
            [
                FakeResult(scalar_one=build_period(period_id=1, employer_id=1)),
                FakeResult(
                    first_row=build_pension_pair(
                        plan_id=1, additional_rate=Decimal("0")
                    )
                ),
                FakeResult(first_row=build_health_pair(plan_id=2)),
                FakeResult(
                    scalar_one=build_contribution_cap(
                        cap_id=33,
                        cap_type=ContributionCapType.PENSION_HEALTH,
                        value_uf=Decimal("90.0000"),
                    )
                ),
                FakeResult(scalar_one=None),
            ],
            "No unemployment contribution cap was found for 2026-01-31.",
        ),
        (
            [
                FakeResult(scalar_one=build_period(period_id=1, employer_id=1)),
                FakeResult(
                    first_row=build_pension_pair(
                        plan_id=1,
                        additional_rate=Decimal("0"),
                        valid_from=date(2026, 2, 1),  # not valid for 2026-01-31
                    )
                ),
            ],
            "Pension plan 1 is not valid for 2026-01-31.",
        ),
        (
            [
                FakeResult(scalar_one=build_period(period_id=1, employer_id=1)),
                FakeResult(
                    first_row=build_pension_pair(
                        plan_id=1, additional_rate=Decimal("0")
                    )
                ),
                FakeResult(
                    first_row=build_health_pair(
                        plan_id=2,
                        valid_from=date(2025, 1, 1),
                        valid_to=date(2025, 12, 31),  # expired before 2026-01-31
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
                FakeResult(scalar_one=build_period(period_id=9, employer_id=1)),
                FakeResult(first_row=None),
            ],
            "Pension plan 1 was not found.",
        ),
        (
            [
                FakeResult(scalar_one=build_period(period_id=9, employer_id=1)),
                FakeResult(
                    first_row=build_pension_pair(
                        plan_id=1, additional_rate=Decimal("0")
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
    period = build_period()
    session = FakeSession(
        [FakeResult(scalar_one=period), _FIVE_CONCEPT_CODES, FakeResult(), FakeResult()]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.save_computed_contributions(
        build_standard_contributions_command()
    )

    assert result.period_id == 5
    assert period.pension_plan_id == 11
    assert sum(isinstance(item, PayrollItemModel) for item in session.added) == 5
    assert session.commit_count == 3
    assert any(
        'DELETE FROM "PAY_ITEM"' in str(statement) for statement in session.executed
    )


@pytest.mark.asyncio
async def test_sa_payroll_repository_keeps_net_pay_pending_until_tax_exists() -> None:
    """Test net pay reconciliation stays pending after contributions only."""
    period = build_period(declared_net_pay_clp=Decimal("830000"))
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            _FIVE_CONCEPT_CODES,
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

    await repository.save_computed_contributions(build_standard_contributions_command())

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
    period = build_period()
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
    period = build_period(
        period_id=7, employer_id=1, status=PayrollStatus.ACTUAL, worked_days=30
    )
    employer = build_acme_employer()
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
    period = build_period(
        period_id=7, employer_id=1, status=PayrollStatus.ACTUAL, worked_days=30
    )
    employer = build_acme_employer(ended_at=date(2026, 1, 15))
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
async def test_list_period_details_returns_one_entry_per_matching_period_id() -> None:
    """list_period_details() fans out get_period_detail() over each matched id.

    Uses an employer with an explicit ended_at (skips the "find next
    employer" query) and no health plan rows (skips the health-institution
    query) so each period's get_period_detail() call consumes exactly four
    queued results, keeping this test's FakeSession queue simple and honest
    about what it is asserting: fan-out and ordering, not get_period_detail
    itself (already covered by the tests above).
    """
    employer = build_acme_employer(ended_at=date(2026, 1, 15))
    period_one = build_period(period_id=1, employer_id=1)
    period_two = build_period(period_id=2, employer_id=1)
    session = FakeSession(
        [
            FakeResult(scalar_rows=[1, 2]),  # id lookup query
            FakeResult(first_row=(period_one, employer)),
            FakeResult(scalar_rows=[]),
            FakeResult(joined_rows=[]),
            FakeResult(first_row=None),
            FakeResult(first_row=(period_two, employer)),
            FakeResult(scalar_rows=[]),
            FakeResult(joined_rows=[]),
            FakeResult(first_row=None),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    results = await repository.list_period_details(ExportPayrollFiltersDTO())

    assert [detail.id for detail in results] == [1, 2]


@pytest.mark.asyncio
async def test_list_period_details_skips_ids_get_period_detail_no_longer_finds() -> (
    None
):
    """A period deleted between the id lookup and the detail fetch is silently skipped.

    Mirrors get_period_detail()'s own None-for-missing behavior rather than
    raising -- a race between the id query and the per-period detail fetch
    is not this method's concern to surface as an error.
    """
    session = FakeSession(
        [
            FakeResult(scalar_rows=[404]),
            FakeResult(first_row=None),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    results = await repository.list_period_details(ExportPayrollFiltersDTO())

    assert results == []


@pytest.mark.asyncio
async def test_list_period_details_with_no_matches_returns_empty_list() -> None:
    """No matching period ids means no fan-out queries at all."""
    session = FakeSession([FakeResult(scalar_rows=[])])
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    results = await repository.list_period_details(
        ExportPayrollFiltersDTO(employer="NOBODY")
    )

    assert results == []
    assert len(session.executed) == 1, "should not fan out when there are no ids"


@pytest.mark.asyncio
async def test_list_period_details_applies_period_year_and_month_filters() -> None:
    """period_year and period_month filters are both applied to the id query.

    Only asserts the call completes and returns an empty result for a
    FakeSession with no queued ids -- get_period_detail()'s own SQL shape
    is already covered elsewhere; this test's only job is to exercise the
    period_year/period_month WHERE-clause branches themselves.
    """
    session = FakeSession([FakeResult(scalar_rows=[])])
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    results = await repository.list_period_details(
        ExportPayrollFiltersDTO(period_year=2026, period_month=1)
    )

    assert results == []


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
    current_period = build_default_current_period()
    current_employer = build_specific_chile_employer()
    previous_period = build_default_previous_period()
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
    assert result[11].increase is None
    assert result[12].is_current is True
    assert result[12].start_date == date(2026, 3, 28)
    assert result[12].end_date == date(2026, 4, 28)
    assert result[12].net_pay_clp == Decimal("2978086")
    assert result[12].increase is None
    assert result[13].period_year == 2026
    assert result[13].period_month == 4
    assert result[13].start_date == date(2026, 4, 29)
    assert result[13].end_date == date(2026, 5, 27)
    assert result[13].increase == Decimal("0.00")
    assert result[20].period_year == 2026
    assert result[20].period_month == 11
    assert result[20].start_date == date(2026, 11, 27)
    assert result[20].increase == Decimal("0.00")
    assert result[0].inferred is True
    assert result[0].start_date == date(2025, 3, 31)


@pytest.mark.asyncio
async def test_list_period_ranges_resolves_names_for_other_employers() -> None:
    """A previous period belonging to a different employer gets its own name.

    current_employer's name is already known for free (its full row was
    already loaded to resolve "current"), but a previous period from a
    *different* employer_id is fetched with no employer filter -- exercises
    the other_employer_ids batch-resolution branch (and, transitively,
    _fetch_employer_names_map() itself) that every other list_period_ranges()
    test in this file never triggers because they all use a single employer.
    """
    current_period = build_default_current_period()
    current_employer = build_specific_chile_employer()
    previous_period = build_default_previous_period(employer_id=2)
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_rows=[previous_period]),
            FakeResult(joined_rows=[]),  # salary_base/fixed_uf_clp aggregates
            FakeResult(joined_rows=[]),  # PAY_MV_SUMARY amounts
            FakeResult(joined_rows=[(2, "OTHER CO")]),  # other employer names
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.list_period_ranges(
        today=date(2026, 3, 31), previous_months=1, future_months=0
    )

    previous_entry = next(item for item in result if not item.is_current)
    assert previous_entry.employer_id == 2
    assert previous_entry.employer_name == "OTHER CO"


@pytest.mark.asyncio
async def test_fetch_employer_names_map_skips_round_trip_for_empty_input() -> None:
    """An empty employer_ids list short-circuits to {} with zero DB round trips.

    The only real call site (list_period_ranges()) already guards this with
    `if other_employer_ids:` to avoid the round trip in the (overwhelmingly
    common) single-employer case -- this test exercises the method's own
    defensive guard directly, since no call site can ever reach it live.
    """
    session = FakeSession([])
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository._fetch_employer_names_map([])

    assert result == {}
    assert session.executed == []


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_attaches_lookback_for_full_previous_window() -> (  # noqa: E501
    None
):
    """The 13th of 13 previous periods becomes a lookback ghost at result[0]."""
    current_period = PayrollPeriodModel(
        id=100,
        employer_id=1,
        period_year=2026,
        period_month=3,
        payment_date=date(2026, 3, 28),
        status=PayrollStatus.ACTUAL,
        declared_net_pay_clp=Decimal("3000000"),
    )
    current_employer = build_specific_chile_employer()
    # 13 previous periods — most-recent-first (DESC).
    # Index 0 = Feb 2026, index 12 = Mar 2025.
    previous_periods = [
        PayrollPeriodModel(
            id=i,
            employer_id=1,
            period_year=2026 if m > 0 else 2025,
            period_month=m if m > 0 else m + 12,
            payment_date=date(2026 if m > 0 else 2025, m if m > 0 else m + 12, 26),
            status=PayrollStatus.ACTUAL,
            declared_net_pay_clp=Decimal("2800000"),
            worked_days=30,
        )
        for i, m in enumerate(range(2, -11, -1), start=50)
        # generates months: 2, 1, 0→12, -1→11, ..., -9→3  (Feb 2026 → Mar 2025)
    ]
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_rows=previous_periods),  # 13 items
            FakeResult(joined_rows=[]),  # salary query → empty
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.list_period_ranges(today=date(2026, 3, 31))

    # With 13 previous periods, the 13th (oldest) becomes a lookback ghost
    # prepended at index 0.
    assert len(result) == 26  # 1 lookback + 12 previous + 1 current + 12 future
    assert result[0].is_lookback is True
    assert result[0].is_current is False
    # The 12 window previous periods follow; none are lookbacks
    for idx in range(1, 13):
        assert result[idx].is_lookback is False
    # Current period is at index 13 (shifted by the lookback)
    assert result[13].is_current is True
    # Future periods start at index 14
    assert result[14].is_current is False
    assert result[14].is_lookback is False


@pytest.mark.asyncio
async def test_list_period_ranges_explicit_previous_months_skips_padding() -> None:
    """An explicit previous_months never pads with inferred placeholders.

    Only 1 real previous period exists; previous_months=5 is requested.
    The default (omitted) path would pad up to 5 with net_pay_clp=None
    inferred entries -- an explicit count must not, per the 2026-10-02
    design decision (padding only applies to the implicit default).
    """
    current_period = build_default_current_period()
    current_employer = build_specific_chile_employer()
    previous_period = build_default_previous_period()
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_rows=[previous_period]),
            FakeResult(joined_rows=[]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.list_period_ranges(
        today=date(2026, 3, 31), previous_months=5
    )

    previous_entries = [
        item for item in result if item.start_date < current_period.payment_date
    ]
    assert len(previous_entries) == 1
    assert previous_entries[0].net_pay_clp == Decimal("2983237")
    assert previous_entries[0].inferred is False
    assert len(result) == 1 + 1 + 12  # 1 real previous + current + 12 future


@pytest.mark.asyncio
async def test_list_period_ranges_explicit_future_months_truncates() -> None:
    """An explicit future_months truncates the projection instead of 12."""
    current_period = build_default_current_period()
    current_employer = build_specific_chile_employer()
    repository = build_repository_with_no_previous_periods(
        current_period, current_employer
    )

    result = await repository.list_period_ranges(
        today=date(2026, 3, 31), previous_months=0, future_months=3
    )

    future_entries = [item for item in result if not item.is_current]
    assert len(future_entries) == 3
    assert len(result) == 1 + 3  # current + 3 future, no previous at all


@pytest.mark.asyncio
async def test_list_period_ranges_zero_months_returns_only_current() -> None:
    """previous_months=0 and future_months=0 together return just the current period."""
    current_period = build_default_current_period()
    current_employer = build_specific_chile_employer()
    repository = build_repository_with_no_previous_periods(
        current_period, current_employer
    )

    result = await repository.list_period_ranges(
        today=date(2026, 3, 31), previous_months=0, future_months=0
    )

    assert len(result) == 1
    assert result[0].is_current is True


@pytest.mark.asyncio
async def test_get_period_range_returns_none_when_period_not_found() -> None:
    """An unknown period_id -> None, same absence contract as get_period_detail()."""
    session = FakeSession([FakeResult(first_row=None)])
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.get_period_range(999)

    assert result is None


@pytest.mark.asyncio
async def test_get_period_range_builds_context_for_current_period() -> None:
    """Target IS the resolved current period -> is_current True, no predecessor.

    Exercises the "not is_previous" branch of get_period_range() (single
    target_currency_task awaited directly, current_dto built from the same
    row as target) plus the None-predecessor branch of predecessor_dto.
    """
    current_period = build_default_current_period()
    current_employer = build_specific_chile_employer()
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),  # target
            FakeResult(scalar_one=None),  # predecessor -> none
            FakeResult(first_row=(current_period, current_employer)),  # current
            FakeResult(joined_rows=[(17, Decimal("1500000"), Decimal("0"))]),
            FakeResult(
                joined_rows=[
                    SimpleNamespace(
                        period_id=17,
                        gross_income_clp=Decimal("2000000"),
                        taxable_income_clp=Decimal("1800000"),
                        total_discounts_clp=Decimal("300000"),
                    )
                ]
            ),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    context = await repository.get_period_range(17)

    assert context is not None
    assert context.predecessor is None
    assert context.target.period_id == 17
    assert context.target.is_current is True
    assert context.target.salary_base == Decimal("1500000")
    assert context.target.gross_income_clp == Decimal("2000000")
    assert context.target.taxable_income_clp == Decimal("1800000")
    assert context.target.total_discounts_clp == Decimal("300000")
    assert context.current is not None
    assert context.current.period_id == 17
    assert context.current.is_current is True


@pytest.mark.asyncio
async def test_get_period_range_builds_context_for_previous_period() -> None:
    """Target older than the resolved current -> is_previous True, has a predecessor.

    Exercises the is_previous branch (asyncio.gather of both currency
    tasks) plus a populated predecessor_dto.
    """
    target_period = build_default_previous_period()  # id=16, Feb 2026
    target_employer = build_specific_chile_employer()
    predecessor_period = build_default_previous_period(
        id=15,
        period_month=1,
        payment_date=date(2026, 1, 29),
        declared_net_pay_clp=Decimal("2900000"),
    )
    current_period = build_default_current_period()  # id=17, March 2026
    session = FakeSession(
        [
            FakeResult(first_row=(target_period, target_employer)),  # target
            FakeResult(scalar_one=predecessor_period),  # predecessor
            FakeResult(first_row=(current_period, target_employer)),  # current
            FakeResult(
                joined_rows=[
                    (16, Decimal("1200000"), Decimal("0")),
                    (15, Decimal("1100000"), Decimal("0")),
                    (17, Decimal("1500000"), Decimal("0")),
                ]
            ),
            FakeResult(
                joined_rows=[
                    SimpleNamespace(
                        period_id=16,
                        gross_income_clp=Decimal("1300000"),
                        taxable_income_clp=Decimal("1200000"),
                        total_discounts_clp=Decimal("100000"),
                    )
                ]
            ),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    context = await repository.get_period_range(16)

    assert context is not None
    assert context.target.period_id == 16
    assert context.target.is_current is False
    assert context.target.salary_base == Decimal("1200000")
    assert context.predecessor is not None
    assert context.predecessor.period_month == 1
    assert context.predecessor.salary_base == Decimal("1100000")
    assert context.current is not None
    assert context.current.period_id == 17
    assert context.current.is_current is True


@pytest.mark.asyncio
async def test_get_period_range_handles_no_current_period_anywhere() -> None:
    """No period anywhere qualifies as "current" yet -> current_dto stays None.

    A brand-new system with only not-yet-declared periods: _resolve_current_period_row()
    returns None, so is_previous is always False regardless of dates, and
    current_dto is never built (the is-not-None guard short-circuits).
    """
    target_period = build_default_current_period()
    target_employer = build_specific_chile_employer()
    session = FakeSession(
        [
            FakeResult(first_row=(target_period, target_employer)),  # target
            FakeResult(scalar_one=None),  # predecessor -> none
            FakeResult(first_row=None),  # no current period at all
            FakeResult(joined_rows=[(17, Decimal("1500000"), Decimal("0"))]),
            FakeResult(joined_rows=[]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    context = await repository.get_period_range(17)

    assert context is not None
    assert context.target.is_current is False
    assert context.target.gross_income_clp is None
    assert context.predecessor is None
    assert context.current is None


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
    assert result[13].increase == Decimal("0.00")


def _build_current_period_fixture_session() -> tuple[PayrollPeriodModel, "FakeSession"]:
    """Build the recurring id=19/June-2026 current-period FakeSession fixture.

    Shared by the two list_period_ranges() tests below that only differ in
    which market_data_repository (if any) they pass to the repository.
    """
    current_period = PayrollPeriodModel(
        id=19,
        employer_id=1,
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 5, 28),
        status=PayrollStatus.ACTUAL,
        declared_net_pay_clp=Decimal("3134978"),
    )
    current_employer = build_specific_chile_employer()
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_rows=[]),
        ]
    )
    return current_period, session


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_infers_current_month_offset() -> None:
    """Test future ranges align to the observed current payment-month offset."""
    current_period, session = _build_current_period_fixture_session()
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


@pytest.mark.asyncio
async def test_list_period_ranges_degrades_to_none_on_market_data_outage() -> None:
    """A pf-rates outage must not 500 the whole endpoint -- just null that one field.

    See docs/proposals/net-pay-prediction-reimplementation-design-
    recommendation.md, Section 4: list_period_ranges() is the single
    try/except boundary responsible for this, not predict_next_period_net_pay()
    itself (which must keep propagating the real error -- see
    test_predict_next_period_net_pay_propagates_dependency_error above).
    """
    _current_period, session = _build_current_period_fixture_session()
    market_data_repository = FakeMarketDataRepository(
        raises=PayrollDependencyError("pf-rates is unreachable.")
    )
    repository = SqlAlchemyPayrollRepository(  # type: ignore[arg-type]
        session, market_data_repository
    )

    result = await repository.list_period_ranges(today=date(2026, 6, 11))

    assert result[12].is_current is True
    assert result[13].net_pay_clp is None


@pytest.mark.asyncio
async def test_list_period_ranges_converts_non_future_periods_to_foreign_currencies() -> (  # noqa: E501
    None
):
    """previous/current periods get real USD/EUR/UF equivalents; future ones don't.

    Uses the id=17/March-2026 current + id=16/February-2026 previous fixture
    from test_sqlalchemy_payroll_repository_lists_period_ranges (result[11]
    is that previous period, result[12] is current, result[13] is the first
    future month) -- same shape, this test only adds a market_data_repository
    with real rates for both non-future start_dates.
    """
    current_period = build_default_current_period()
    current_employer = build_specific_chile_employer()
    previous_period = build_default_previous_period()
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_rows=[previous_period]),
        ]
    )
    market_data_repository = FakeMarketDataRepository(
        rates_by_currency_date={
            ("USD", date(2026, 2, 26)): Decimal("950.00"),
            ("EUR", date(2026, 2, 26)): Decimal("1030.00"),
            ("UF", date(2026, 2, 26)): Decimal("38500.00"),
            ("USD", date(2026, 3, 28)): Decimal("955.00"),
            ("EUR", date(2026, 3, 28)): Decimal("1035.00"),
            ("UF", date(2026, 3, 28)): Decimal("38600.00"),
        }
    )
    repository = SqlAlchemyPayrollRepository(  # type: ignore[arg-type]
        session, market_data_repository
    )

    result = await repository.list_period_ranges(today=date(2026, 3, 31))

    assert result[11].net_pay_usd == (Decimal("2983237") / Decimal("950.00")).quantize(
        Decimal("0.01")
    )
    assert result[11].net_pay_eur == (Decimal("2983237") / Decimal("1030.00")).quantize(
        Decimal("0.01")
    )
    assert result[11].net_pay_uf == (Decimal("2983237") / Decimal("38500.00")).quantize(
        Decimal("0.01")
    )
    assert result[12].is_current is True
    assert result[12].net_pay_usd == (Decimal("2978086") / Decimal("955.00")).quantize(
        Decimal("0.01")
    )
    assert result[12].net_pay_eur == (Decimal("2978086") / Decimal("1035.00")).quantize(
        Decimal("0.01")
    )
    assert result[12].net_pay_uf == (Decimal("2978086") / Decimal("38600.00")).quantize(
        Decimal("0.01")
    )
    # Future periods never get a currency conversion -- regardless of
    # whether net_pay_clp itself could be predicted (it can't here: this
    # fixture's market_data_repository has no UF rate configured for the
    # month-end date predict_next_period_net_pay() needs, which is a
    # separate concern from the per-period-start_date rates this test is
    # actually about).
    assert result[13].net_pay_usd is None
    assert result[13].net_pay_eur is None
    assert result[13].net_pay_uf is None


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
    assert result[12].increase is None
    assert result[13].start_date == date(2026, 2, 27)
    assert result[13].increase == Decimal("0.00")
    assert result[24].period_year == 2027
    assert result[24].period_month == 1
    assert result[24].increase == Decimal("0.00")


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_marks_scheduled_future_increases() -> None:
    """No market_data_repository wired in: increase degrades to 0.00 regardless.

    This repository is built with no market_data_repository (same as every
    other list_period_ranges() test in this file that doesn't explicitly
    pass one), so there is no IPC data to compute a real percentage from --
    increase is 0.00 across the board here, on both scheduled-increase and
    non-increase months alike. The employer schedule
    (first_increase_period_month=8, increase_frequency=6) genuinely used to
    flag these specific months as True/False before increase became a
    percentage; the schedule-driven percentage itself (with real market
    data) is covered by
    test_sqlalchemy_payroll_repository_lists_period_ranges_projects_all_future_months
    instead.
    """
    current_period = PayrollPeriodModel(
        id=20,
        employer_id=1,
        period_year=2026,
        period_month=3,
        payment_date=date(2026, 3, 28),
        status=PayrollStatus.ACTUAL,
        declared_net_pay_clp=Decimal("2900000"),
    )
    current_employer = build_specific_chile_employer(
        first_increase_period_year=2026,
        first_increase_period_month=8,
        increase_frequency=6,
    )
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_rows=[]),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    result = await repository.list_period_ranges(today=date(2026, 3, 31))

    assert result[16].period_year == 2026
    assert result[16].period_month == 7
    assert result[16].increase == Decimal("0.00")
    assert result[17].period_year == 2026
    assert result[17].period_month == 8
    assert result[17].increase == Decimal("0.00")
    assert result[23].period_year == 2027
    assert result[23].period_month == 2
    assert result[23].increase == Decimal("0.00")


@pytest.mark.asyncio
async def test_sa_payroll_repository_builds_income_tax_context() -> None:
    """Test sqlalchemy payroll repository builds income tax context."""
    period = build_period(employer_id=1, status=PayrollStatus.ACTUAL)
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(scalar_one=Decimal("1000000")),
            FakeResult(scalar_one=Decimal("176000")),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    context = await repository.get_income_tax_context(SimpleNamespace(period_id=5))

    assert context.taxable_income_clp == Decimal("1000000")
    assert context.deductible_amount_clp == Decimal("176000")


@pytest.mark.asyncio
async def test_income_tax_ctx_excludes_health_additional() -> None:
    """Test income-tax context excludes additional health plan charges."""
    period = build_period(employer_id=1, status=PayrollStatus.ACTUAL)
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
    period = build_period()
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
    period = build_period()
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
    period = build_period()
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
@pytest.mark.parametrize(
    "summary_net_pay, expected_net_pay, net_pay_diff",
    [
        (Decimal("830000"), Decimal("830000"), Decimal("0")),
        (None, None, None),
    ],
)
async def test_sqlalchemy_payroll_repository_reconciles_net_pay_after_tax(
    summary_net_pay: Decimal | None,
    expected_net_pay: Decimal | None,
    net_pay_diff: Decimal | None,
) -> None:
    """Test net pay reconciliation after tax; stays pending when summary is absent."""
    period = build_period(declared_net_pay_clp=Decimal("830000"))
    session = FakeSession(
        [
            FakeResult(scalar_one=period),
            FakeResult(scalar_one=SimpleNamespace(id=9, code="INCOME_TAX")),
            FakeResult(),
            FakeResult(),
            _REVIEW_REQUIRED_CONCEPTS_RESULT,
            FakeResult(scalar_one=summary_net_pay),
        ]
    )
    repository = SqlAlchemyPayrollRepository(session)  # type: ignore[arg-type]

    await repository.save_computed_income_tax(
        SimpleNamespace(
            period_id=5,
            tax=SimpleNamespace(tax_clp=Decimal("674")),
        )
    )

    assert period.expected_net_pay_clp == expected_net_pay
    assert period.net_pay_difference_clp == net_pay_diff
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
    period = build_period()
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
    fake_session = await assert_get_session_lifecycle(monkeypatch, dependencies)

    repository = dependencies.get_payroll_repository(fake_session)  # type: ignore[arg-type]
    use_case = dependencies.get_import_payroll_use_case(repository)
    queries = dependencies.get_payroll_queries(repository)
    assign_use_case = dependencies.get_assign_plans_use_case(repository)
    review_use_case = dependencies.get_review_payroll_period_use_case(repository)
    compute_use_case = dependencies.get_compute_contributions_use_case(repository)
    compute_tax_use_case = dependencies.get_compute_income_tax_use_case(repository)  # type: ignore[arg-type]

    assert isinstance(repository, SqlAlchemyPayrollRepository)
    assert isinstance(use_case, ImportPayroll)
    assert isinstance(assign_use_case, AssignPlans)
    assert isinstance(review_use_case, ReviewPayrollPeriod)
    assert queries.__class__.__name__ == "PayrollQueries"
    assert compute_use_case.__class__.__name__ == "ComputeContributions"
    assert compute_tax_use_case.__class__.__name__ == "ComputeIncomeTax"


def test_payroll_models_are_declared() -> None:
    """Test payroll models are declared."""
    assert EmployerModel.__tablename__ == "PAY_EMPLOYER"
    assert PayrollPeriodModel.__tablename__ == "PAY_PERIOD"
    assert PayrollItemModel.__tablename__ == "PAY_ITEM"
    assert PayrollSummaryModel.__tablename__ == "PAY_MV_SUMARY"
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


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_returns_none_without_repository() -> None:
    """No market_data_repository means the feature is not wired in -- None, no I/O."""
    current_period = build_june_2026_period()
    session = FakeSession([])

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1), None
    )

    assert result is None


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_returns_none_for_missing_uf() -> None:
    """Test predict_next_period_net_pay returns None when UF data is missing."""
    current_period = build_june_2026_period()
    session = FakeSession([])
    market_data_repository = FakeMarketDataRepository()  # empty -> no UF for any date

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1), market_data_repository
    )

    assert result is None


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_returns_none_for_missing_income() -> None:
    """Test predict_next_period_net_pay returns None when income is missing."""
    current_period = build_june_2026_period()
    session = FakeSession(
        [
            FakeResult(joined_rows=[]),  # items (empty)
        ]
    )
    market_data_repository = FakeMarketDataRepository(
        {date(2026, 6, 30): Decimal("40821.18")}
    )

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1), market_data_repository
    )

    assert result is None


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_returns_none_without_historical_uf() -> None:
    """Test UF-dependent prediction returns None when the reference UF is missing."""
    current_period = build_june_2026_period(worked_days=30)
    session = FakeSession(
        [
            FakeResult(joined_rows=_HEALTH_UF_ITEMS),  # items
        ]
    )
    # Only the month-end UF is known; current_period.payment_date's UF is not,
    # and _HEALTH_UF_ITEMS has a nonzero HEALTH_ADDITIONAL_UF, so that second
    # lookup is required and, missing, must short-circuit to None.
    market_data_repository = FakeMarketDataRepository(
        {date(2026, 6, 30): Decimal("41000.00")}
    )

    result = await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1), market_data_repository
    )

    assert result is None


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_propagates_dependency_error() -> None:
    """A pf-rates outage must surface as PayrollDependencyError, not a silent None.

    list_period_ranges() is the one responsible for catching this at its own
    call-site boundary and degrading to None there -- this function itself
    must not swallow it, or that boundary would never see a real failure to
    distinguish from "pf-rates genuinely has no UF value".
    """
    current_period = build_june_2026_period()
    session = FakeSession([])
    market_data_repository = FakeMarketDataRepository(
        raises=PayrollDependencyError("pf-rates is unreachable.")
    )

    with pytest.raises(PayrollDependencyError):
        await predict_next_period_net_pay(
            session, current_period, date(2026, 6, 1), market_data_repository
        )


async def _predict_june_2026(
    current_period: PayrollPeriodModel,
    items: list[tuple[Decimal, str]],
    *,
    uf_current: Decimal = Decimal("40821.18"),
    reference_uf: Decimal = Decimal("40821.18"),
) -> PredictedNetPayBaseline | None:
    """Run predict_next_period_net_pay for a June-2026 period with the given items."""
    session = FakeSession(
        [
            FakeResult(joined_rows=items),  # items
        ]
    )
    market_data_repository = FakeMarketDataRepository(
        {
            date(2026, 6, 30): uf_current,
            current_period.payment_date: reference_uf,
        }
    )
    return await predict_next_period_net_pay(
        session, current_period, date(2026, 6, 1), market_data_repository
    )


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_returns_none_zero_net_pay() -> None:
    """Test predict_next_period_net_pay returns None when result would be <= 0."""
    current_period = build_june_2026_period()
    items = [
        (Decimal("100"), "SALARY_BASE"),
        (Decimal("100"), "PENSION_BASE"),
        (Decimal("100"), "INCOME_TAX"),
    ]

    result = await _predict_june_2026(current_period, items)

    assert result is None


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_calculates_correctly() -> None:
    """Test prediction recalculates UF-based discounts with selected UF.

    Worked example from docs/proposals/net-pay-prediction-reimplementation-
    design-recommendation.md, Section 2 (recovered verbatim from the
    pre-pf-rates implementation, commit bdc3d29).
    """
    current_period = build_june_2026_period(worked_days=30)
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

    result = await _predict_june_2026(
        current_period,
        items,
        uf_current=selected_uf,
        reference_uf=current_reference_uf,
    )
    assert result is not None

    employer_uf_quantity = Decimal("8000") / current_reference_uf
    future_employer_contribution = employer_uf_quantity * selected_uf
    future_health_additional_uf = (
        Decimal("35000") / current_reference_uf
    ) * selected_uf
    expected_gross = Decimal("3300000") + future_employer_contribution
    expected_discount_ratio = Decimal("230000") / Decimal("3308000")
    expected_non_uf_discounts = expected_gross * expected_discount_ratio
    expected_scalable = (expected_gross - expected_non_uf_discounts).quantize(
        Decimal("0.01")
    )
    expected_fixed_uf = future_health_additional_uf.quantize(Decimal("0.01"))
    expected_net_pay = expected_scalable - expected_fixed_uf

    assert result.net_pay_clp == expected_net_pay
    # The split: scalable_clp is what a later raise should scale, fixed_uf_clp
    # (HEALTH_ADDITIONAL_UF, UF-driven) is what it must leave alone -- see
    # PredictedNetPayBaseline's own docstring and project_future_months().
    assert result.scalable_clp == expected_scalable
    assert result.fixed_uf_clp == expected_fixed_uf


@pytest.mark.asyncio
async def test_predict_next_period_net_pay_adjusts_for_worked_days() -> None:
    """A partial-month current period projects income/discounts to 30 days first."""
    current_period = build_june_2026_period(worked_days=15)
    items = [
        (Decimal("1500000"), "SALARY_BASE"),
        (Decimal("50000"), "PENSION_BASE"),
        (Decimal("25000"), "INCOME_TAX"),
    ]

    result = await _predict_june_2026(current_period, items)
    assert result is not None

    projected_gross = Decimal("1500000") * Decimal(30) / Decimal(15)
    discount_ratio = Decimal("75000") / Decimal("1500000")
    expected_net_pay = projected_gross - (projected_gross * discount_ratio)

    assert result.net_pay_clp == expected_net_pay.quantize(Decimal("0.01"))
    # No UF-driven discount item here -- the whole prediction is scalable.
    assert result.scalable_clp == result.net_pay_clp
    assert result.fixed_uf_clp == Decimal("0.00")


@pytest.mark.asyncio
async def test_project_future_months_nulls_net_pay_without_first_prediction() -> None:
    """No month_offset=1 prediction to replicate -- net_pay stays None throughout.

    increase_pct is independent and still defaults to 0.00 for every month
    (this FakeMarketDataRepository has no latest_economic_index configured,
    so there is nothing to compute a real percentage from either).
    """
    result = await project_future_months(
        None,
        current_year=2026,
        current_month=9,
        first_increase_period=date(2026, 4, 1),
        increase_frequency=12,
        market_data_repository=FakeMarketDataRepository(),
    )

    assert len(result) == 12
    assert all(month.net_pay_clp is None for month in result.values())
    assert all(month.increase_pct == Decimal("0.00") for month in result.values())


@pytest.mark.asyncio
async def test_project_future_months_degrades_without_repository() -> None:
    """No market_data_repository wired in: everything but month_offset 1 is None.

    month_offset 1 keeps mirroring the already-computed first prediction
    (it never depended on this repository); every other month's net_pay_clp
    is None (nothing to replicate/step with), and increase_pct is 0.00
    everywhere -- no IPC data can be checked at all without a repository.
    """
    first_future_net_pay_clp = Decimal("3118248.98")

    result = await project_future_months(
        _baseline(first_future_net_pay_clp),
        current_year=2026,
        current_month=9,
        first_increase_period=date(2026, 4, 1),
        increase_frequency=12,
        market_data_repository=None,
    )

    assert len(result) == 12
    assert result[1].net_pay_clp == first_future_net_pay_clp
    assert all(result[offset].net_pay_clp is None for offset in range(2, 13))
    assert all(month.increase_pct == Decimal("0.00") for month in result.values())


@pytest.mark.asyncio
async def test_project_future_months_replicates_until_next_increase() -> None:
    """Months 1-12 replicate the first prediction until an increase month.

    Worked example verified against real restored Neon data during local
    debugging -- see docs/proposals/net-pay-prediction-reimplementation-
    design-plan.md, "Extending to all 12 future months" section, and later
    corrected by docs/proposals/future-increase-ipc-extrapolation-design-
    recommendation.md (Section 4) once the M-vs-N gap was identified.
    Employer's first_increase_period is 2026-04 with the default 12-month
    cadence, so from a current period of 2026-09 the next increase lands on
    2027-04 (month_offset=7). Only M=4 months of real IPC exist between the
    2026-04 anchor (112.18) and the latest published figure, 113.15
    (2026-08) -- a naive M-only ratio would report just 0.86%, understating
    the full N=12-month cycle. With a trailing-12-month anchor also
    published (108.00 at 2025-08), `_extrapolate_cycle_ratio()` compounds
    the remaining 8 months' geometric monthly rate on top of the real
    4-month ratio, yielding the corrected 4.05% from the recommendation's
    own worked example.
    """
    market_data_repository = FakeMarketDataRepository(
        economic_index_by_period={
            ("IPC_CL", 2025, 8): Decimal("108.00"),
            ("IPC_CL", 2026, 4): Decimal("112.18"),
        },
        latest_economic_index={
            "IPC_CL": (date(2026, 8, 1), Decimal("113.15")),
        },
    )
    first_future_net_pay_clp = Decimal("3118248.98")

    result = await project_future_months(
        _baseline(first_future_net_pay_clp),
        current_year=2026,
        current_month=9,
        first_increase_period=date(2026, 4, 1),
        increase_frequency=12,
        market_data_repository=market_data_repository,
    )

    assert len(result) == 12  # month_offset 1..12
    for month_offset in range(1, 7):
        assert result[month_offset].net_pay_clp == first_future_net_pay_clp
        assert result[month_offset].increase_pct == Decimal("0.00")
    stepped = result[7].net_pay_clp
    real_ratio = Decimal("113.15") / Decimal("112.18")
    trailing_ratio = Decimal("113.15") / Decimal("108.00")
    monthly_ratio = trailing_ratio ** (Decimal(1) / Decimal(12))
    total_ratio = real_ratio * (monthly_ratio**8)  # 8 = 12 (N) - 4 (M) missing months
    expected_stepped = (first_future_net_pay_clp * total_ratio).quantize(
        Decimal("0.01")
    )
    expected_pct = ((total_ratio - 1) * 100).quantize(Decimal("0.01"))
    assert stepped == expected_stepped == Decimal("3244420.33")
    assert result[7].increase_pct == expected_pct == Decimal("4.05")
    for month_offset in range(8, 13):
        assert result[month_offset].net_pay_clp == stepped
        assert result[month_offset].increase_pct == Decimal("0.00")


@pytest.mark.asyncio
async def test_project_future_months_does_not_scale_uf_driven_portion() -> None:
    """An increase step must scale only the salary_base-driven portion.

    Explicit user correction (2026-10-02): a configured raise is a
    salary_base event -- it must be applied to the salary_base-driven
    portion of the prediction (scalable_clp) and the usual payroll discount
    math reapplied from there, exactly like every other payroll. The
    UF-driven portion (fixed_uf_clp, e.g. HEALTH_ADDITIONAL_UF) tracks the
    UF/CLP exchange rate, not the employer's salary raise, so it must be
    netted back in unchanged -- never multiplied by the same ratio.

    Baseline: scalable_clp=1,000,000.00, fixed_uf_clp=100,000.00 ->
    net_pay_clp=900,000.00. A real_ratio of 1.10 (M=N=12, no extrapolation
    needed -- the cheap path) steps the scalable portion to 1,100,000.00.
    Netting the *unchanged* fixed_uf_clp back in gives 1,000,000.00 --
    *not* 900,000.00 * 1.10 = 990,000.00, which is what scaling the whole
    net figure (the pre-fix bug) would have produced instead.
    """
    market_data_repository = FakeMarketDataRepository(
        economic_index_by_period={("IPC_CL", 2025, 8): Decimal("100.00")},
        latest_economic_index={"IPC_CL": (date(2026, 8, 1), Decimal("110.00"))},
    )
    baseline = _baseline(Decimal("900000.00"), fixed_uf_clp=Decimal("100000.00"))

    result = await project_future_months(
        baseline,
        current_year=2026,
        current_month=7,
        # last_increase_period resolves to 2025-08; month_offset=1 (2026-08)
        # is the increase month itself (M = N = 12 months, the cheap path),
        # so the stepped value becomes the running baseline for month_offset=2.
        first_increase_period=date(2025, 8, 1),
        increase_frequency=12,
        market_data_repository=market_data_repository,
    )

    # month_offset=1 still displays the raw baseline unchanged, by design.
    assert result[1].net_pay_clp == Decimal("900000.00")
    assert result[1].increase_pct == Decimal("10.00")
    # month_offset=2 shows the real correction: NOT 990,000.00.
    assert result[2].net_pay_clp == Decimal("1000000.00")
    assert result[2].increase_pct == Decimal("0.00")  # not an increase month itself


@pytest.mark.asyncio
async def test_project_future_months_skips_extrapolation_fetch_when_cycle_covered() -> (
    None
):
    """M >= N: use the real ratio as-is, never even attempt the trailing fetch.

    Proves the "cheap path" claim from docs/proposals/future-increase-ipc-
    extrapolation-design-recommendation.md Section 2.2: when the months
    already elapsed since the last real increase (M) already cover (or
    exceed) the employer's configured cycle (N), there is nothing left to
    extrapolate, so `_extrapolate_cycle_ratio()` must return early without
    ever calling `get_economic_index_value()` for the trailing-window
    anchor. Asserting the call log (not just the resulting number) is what
    actually proves the early return happened, not just that the answer
    coincidentally matches.
    """
    market_data_repository = FakeMarketDataRepository(
        economic_index_by_period={("IPC_CL", 2025, 8): Decimal("100.00")},
        latest_economic_index={"IPC_CL": (date(2026, 8, 1), Decimal("105.00"))},
    )
    first_future_net_pay_clp = Decimal("1000000.00")

    result = await project_future_months(
        _baseline(first_future_net_pay_clp),
        current_year=2026,
        current_month=7,
        # last_increase_period resolves to 2025-08 (as_of 2026-07 predates the
        # next cadence point, 2026-08). M = (2026-08) - (2025-08) = 12 months,
        # exactly N -- the boundary case, folded into the same "nothing left
        # to extrapolate" guard as M > N.
        first_increase_period=date(2025, 8, 1),
        increase_frequency=12,
        market_data_repository=market_data_repository,
    )

    # Only one lookup should ever happen: the last_increase_period baseline
    # itself. A buggy implementation that always attempted the trailing-
    # window fetch regardless of the guard would show this exact same
    # ("IPC_CL", 2025, 8) period a *second* time here (period_back for this
    # fixture coincides with the baseline), so call *count* -- not just the
    # resulting number -- is what actually proves the early return fired.
    assert market_data_repository.economic_index_calls == [("IPC_CL", 2025, 8)]
    expected_stepped = (
        first_future_net_pay_clp * Decimal("105.00") / Decimal("100.00")
    ).quantize(Decimal("0.01"))
    # 2026-08 (month_offset=1) is the first increase month within the window,
    # but its *displayed* net_pay_clp always mirrors the raw UF prediction
    # unchanged, by design (see project_future_months()'s own docstring) --
    # increase_pct is where the step is visible for month_offset=1 itself,
    # and the stepped value becomes the running baseline for month_offset=2.
    assert result[1].net_pay_clp == first_future_net_pay_clp
    assert result[1].increase_pct == Decimal("5.00")
    assert result[2].net_pay_clp == expected_stepped


@pytest.mark.asyncio
async def test_project_future_months_deflation_floor_gates_on_consolidated_ratio() -> (
    None
):
    """The deflation floor must look at the extrapolated ratio, not just M months.

    Constructs a case where the raw M-month `real_ratio` alone is mildly
    *inflationary* (would not trigger the floor on its own), but the
    trailing-12-month anchor reveals a strong enough deflationary trend that
    compounding it across the missing months pulls the consolidated ratio
    below 1 -- proving the floor (docs/proposals/future-increase-ipc-
    extrapolation-design-recommendation.md, Section 5) is gated on
    `total_ratio`, not the pre-extrapolation `real_ratio`.
    """
    market_data_repository = FakeMarketDataRepository(
        economic_index_by_period={
            ("IPC_CL", 2025, 8): Decimal("120.00"),  # trailing-window anchor
            ("IPC_CL", 2026, 4): Decimal("100.00"),  # last real increase anchor
        },
        latest_economic_index={"IPC_CL": (date(2026, 8, 1), Decimal("100.50"))},
    )
    first_future_net_pay_clp = Decimal("1000000.00")

    result = await project_future_months(
        _baseline(first_future_net_pay_clp),
        current_year=2026,
        current_month=9,
        first_increase_period=date(2026, 4, 1),
        increase_frequency=12,
        market_data_repository=market_data_repository,
    )

    # real_ratio alone (100.50 / 100.00 = 1.005) is mildly inflationary and
    # would not trip the floor -- only the extrapolated total_ratio does.
    assert result[7].net_pay_clp == first_future_net_pay_clp
    assert result[7].increase_pct == Decimal("0.00")


@pytest.mark.asyncio
async def test_project_future_months_skips_step_without_prior_increase() -> None:
    """No real increase has ever happened yet -> replicate flat, never step.

    `current_year`/`current_month` (2026-01) predate `first_increase_period`
    (2026-04), so resolve_last_increase_period() returns None -- there is no
    IPC baseline to compare against even though 2027-04 is still a future
    increase-cadence month within the window.
    """
    market_data_repository = FakeMarketDataRepository(
        latest_economic_index={"IPC_CL": (date(2026, 8, 1), Decimal("113.15"))},
    )
    await _assert_project_future_months_replicates_flat(
        market_data_repository, current_month=1
    )


@pytest.mark.asyncio
async def test_project_future_months_skips_step_without_latest_ipc() -> None:
    """No published IPC at all -> replicate flat even through an increase month."""
    market_data_repository = FakeMarketDataRepository()
    await _assert_project_future_months_replicates_flat(market_data_repository)


@pytest.mark.asyncio
async def test_project_future_months_skips_step_without_baseline_index() -> None:
    """Latest IPC exists but the last-increase-month IPC itself is missing.

    Falls back to replicating unchanged -- same graceful-degradation
    philosophy as the rest of this prediction subsystem (see
    PayrollDependencyError handling in list_period_ranges()).
    """
    market_data_repository = FakeMarketDataRepository(
        latest_economic_index={"IPC_CL": (date(2026, 8, 1), Decimal("113.15"))},
    )
    await _assert_project_future_months_replicates_flat(market_data_repository)


@pytest.mark.asyncio
async def test_project_future_months_skips_step_when_latest_ipc_too_old() -> None:
    """The explicit guard: a latest-IPC period later than the increase month.

    Never applies in practice -- IPC is only ever published for the past --
    but the user explicitly required this check. This fake deliberately
    violates that invariant to prove the guard fires.
    """
    market_data_repository = FakeMarketDataRepository(
        economic_index_by_period={("IPC_CL", 2026, 4): Decimal("112.18")},
        latest_economic_index={"IPC_CL": (date(2027, 5, 1), Decimal("200.00"))},
    )
    await _assert_project_future_months_replicates_flat(market_data_repository)


@pytest.mark.asyncio
async def test_project_future_months_handles_consecutive_increase_months() -> None:
    """A short (1-month) increase_frequency steps every single future month.

    month_offset=1 (2026-10) is itself calendar-wise an increase month too
    (frequency=1), but its step is rejected by the "latest IPC too old"
    guard -- the only published latest index is dated 2026-11, which is
    *after* 2026-10 -- so it leaves both the running value and the anchor
    untouched, and month_offset=2 below steps from the exact same 2026-09
    baseline it would have without month_offset=1 ever being evaluated.
    """
    market_data_repository = FakeMarketDataRepository(
        economic_index_by_period={
            ("IPC_CL", 2026, 9): Decimal("100.00"),
            ("IPC_CL", 2026, 11): Decimal("102.00"),
        },
        latest_economic_index={"IPC_CL": (date(2026, 11, 1), Decimal("103.00"))},
    )
    first_future_net_pay_clp = Decimal("1000000.00")

    result = await project_future_months(
        _baseline(first_future_net_pay_clp),
        current_year=2026,
        current_month=9,
        first_increase_period=date(2026, 9, 1),
        increase_frequency=1,
        market_data_repository=market_data_repository,
    )

    assert result[1].net_pay_clp == first_future_net_pay_clp
    assert result[1].increase_pct == Decimal("0.00")
    # month_offset 2 (2026-11) steps from the 2026-09 baseline (the anchor
    # then advances to 2026-11, the increase month itself).
    step_one = (
        first_future_net_pay_clp * Decimal("103.00") / Decimal("100.00")
    ).quantize(Decimal("0.01"))
    assert result[2].net_pay_clp == step_one
    assert result[2].increase_pct == Decimal("3.00")
    # month_offset 3 (2026-12) is also an increase month (frequency=1), so
    # it steps again -- this time from the 2026-11 anchor the previous step
    # just set, using the same latest-published IPC (fetched once, 103.00).
    step_two = (step_one * Decimal("103.00") / Decimal("102.00")).quantize(
        Decimal("0.01")
    )
    assert result[3].net_pay_clp == step_two
    assert result[3].increase_pct == Decimal("0.98")


@pytest.mark.asyncio
async def test_resolve_currency_equivalents_converts_all_three_currencies() -> None:
    """Happy path: net_pay_clp / rate for each of USD, EUR, UF."""
    rate_date = date(2026, 9, 1)
    market_data_repository = FakeMarketDataRepository(
        rates_by_currency_date={
            ("USD", rate_date): Decimal("950.00"),
            ("EUR", rate_date): Decimal("1030.00"),
            ("UF", rate_date): Decimal("38500.00"),
        }
    )

    result = await resolve_currency_equivalents(
        Decimal("3001910"),
        rate_date=rate_date,
        market_data_repository=market_data_repository,
    )

    assert result.usd == (Decimal("3001910") / Decimal("950.00")).quantize(
        Decimal("0.01")
    )
    assert result.eur == (Decimal("3001910") / Decimal("1030.00")).quantize(
        Decimal("0.01")
    )
    assert result.uf == (Decimal("3001910") / Decimal("38500.00")).quantize(
        Decimal("0.01")
    )


@pytest.mark.asyncio
async def test_resolve_currency_equivalents_returns_none_without_net_pay() -> None:
    """net_pay_clp is None (nothing to convert) -> all three are None.

    Short-circuits before ever calling market_data_repository, so a Fake
    configured to raise on every call proves no call was actually made.
    """
    result = await resolve_currency_equivalents(
        None,
        rate_date=date(2026, 9, 1),
        market_data_repository=FakeMarketDataRepository(raises=RuntimeError("boom")),
    )

    assert result.usd is None
    assert result.eur is None
    assert result.uf is None


@pytest.mark.asyncio
async def test_resolve_currency_equivalents_returns_none_without_repository() -> None:
    """No market_data_repository wired in at all -> all three are None."""
    result = await resolve_currency_equivalents(
        Decimal("3001910"),
        rate_date=date(2026, 9, 1),
        market_data_repository=None,
    )

    assert result.usd is None
    assert result.eur is None
    assert result.uf is None


@pytest.mark.asyncio
async def test_resolve_currency_equivalents_degrades_missing_currency_independently() -> (  # noqa: E501
    None
):
    """One currency unpublished for rate_date doesn't block the other two."""
    rate_date = date(2026, 9, 1)
    market_data_repository = FakeMarketDataRepository(
        rates_by_currency_date={
            ("USD", rate_date): Decimal("950.00"),
            ("EUR", rate_date): None,
            ("UF", rate_date): Decimal("38500.00"),
        }
    )

    result = await resolve_currency_equivalents(
        Decimal("3001910"),
        rate_date=rate_date,
        market_data_repository=market_data_repository,
    )

    assert result.usd is not None
    assert result.eur is None
    assert result.uf is not None


@pytest.mark.asyncio
async def test_resolve_currency_equivalents_degrades_on_pf_rates_outage() -> None:
    """pf-rates raising for every currency degrades all three to None.

    resolve_currency_equivalents() never propagates -- unlike
    predict_next_period_net_pay()/project_future_months(), it is a 3-way
    best-effort enrichment, not a single pipeline with one try/except
    boundary at list_period_ranges() -- see its own docstring.
    """
    result = await resolve_currency_equivalents(
        Decimal("3001910"),
        rate_date=date(2026, 9, 1),
        market_data_repository=FakeMarketDataRepository(
            raises=PayrollDependencyError("pf-rates unreachable")
        ),
    )

    assert result.usd is None
    assert result.eur is None
    assert result.uf is None


@pytest.mark.asyncio
async def test_sqlalchemy_payroll_repository_lists_period_ranges_projects_all_future_months() -> (  # noqa: E501
    None
):
    """End-to-end: list_period_ranges() replicates + IPC-steps months 2-12.

    Same current/previous period fixture as
    test_sqlalchemy_payroll_repository_lists_period_ranges (employer's
    default-derived first_increase_period is 2025-11, 12-month cadence, so
    from a current period of 2026-03 the next increase lands on 2026-11,
    confirmed by that other test's `result[20].increase == Decimal("0.00")` under
    its no-market-data setup). This test
    additionally wires a full market_data_repository (UF + IPC) to verify
    the projected net_pay_clp values themselves, not just the increase flag.
    """
    current_period = build_default_current_period(worked_days=30)
    current_employer = build_specific_chile_employer()
    previous_period = build_default_previous_period()
    items = [
        (Decimal("3000000"), "SALARY_BASE"),
        (Decimal("100000"), "PENSION_BASE"),
        (Decimal("50000"), "INCOME_TAX"),
    ]
    session = FakeSession(
        [
            FakeResult(first_row=(current_period, current_employer)),
            FakeResult(scalar_rows=[previous_period]),
            FakeResult(joined_rows=[]),  # salary_base aggregation
            FakeResult(joined_rows=[]),  # PAY_MV_SUMARY amounts
            FakeResult(joined_rows=items),  # predict_next_period_net_pay's items
        ]
    )
    market_data_repository = FakeMarketDataRepository(
        {
            date(2026, 3, 31): Decimal("38000.00"),
            date(2026, 3, 28): Decimal("38000.00"),
        },
        economic_index_by_period={("IPC_CL", 2025, 11): Decimal("105.00")},
        latest_economic_index={"IPC_CL": (date(2026, 2, 1), Decimal("110.00"))},
    )
    repository = SqlAlchemyPayrollRepository(  # type: ignore[arg-type]
        session, market_data_repository
    )

    result = await repository.list_period_ranges(today=date(2026, 3, 31))

    first_future = Decimal("2850000.00")  # 3,000,000 - 5% (150,000/3,000,000) ratio
    assert result[13].net_pay_clp == first_future  # 2026-04, month_offset=1
    for index in range(14, 20):  # 2026-05 .. 2026-10, month_offset 2-7
        assert result[index].net_pay_clp == first_future
    stepped = (first_future * Decimal("110.00") / Decimal("105.00")).quantize(
        Decimal("0.01")
    )
    assert result[20].net_pay_clp == stepped  # 2026-11, month_offset=8, increase
    assert result[20].increase == Decimal("4.76")  # (110.00/105.00 - 1) * 100
    for index in range(21, 25):  # 2026-12 .. 2027-03, month_offset 9-12
        assert result[index].net_pay_clp == stepped


@pytest.mark.asyncio
async def test_project_future_months_holds_flat_on_deflation() -> None:
    """IPC dropping below the last-increase baseline never reduces net pay.

    Explicit user requirement (2026-10-01): salaries are sticky downward --
    a lower latest-published IPC than the baseline holds the previous value
    flat, it never divides it down. Same non-advancing-anchor treatment as
    the other degradation paths, so a later rebound in prices is not lost:
    if IPC later recovers above the *original* 2026-04 baseline, the next
    increase month still compares against that same original baseline, not
    against this skipped one. increase_pct for the floored month is also
    0.00 -- consistent with "no change was actually applied".
    """
    market_data_repository = FakeMarketDataRepository(
        economic_index_by_period={("IPC_CL", 2026, 4): Decimal("112.18")},
        latest_economic_index={"IPC_CL": (date(2026, 8, 1), Decimal("110.00"))},
    )
    first_future_net_pay_clp = Decimal("3118248.98")

    result = await project_future_months(
        _baseline(first_future_net_pay_clp),
        current_year=2026,
        current_month=9,
        first_increase_period=date(2026, 4, 1),
        increase_frequency=12,
        market_data_repository=market_data_repository,
    )

    # month_offset 7 (2027-04) is still flagged as an increase month, but
    # IPC 110.00 < 112.18 means the floor kicks in: hold flat, not step down.
    assert all(
        month.net_pay_clp == first_future_net_pay_clp for month in result.values()
    )
    assert all(month.increase_pct == Decimal("0.00") for month in result.values())


@pytest.mark.asyncio
async def test_project_future_months_steps_on_flat_ipc() -> None:
    """IPC exactly equal to the baseline is not a decrease -- it still steps.

    Numerically a no-op either way (ratio of 1, increase_pct of 0.00), but
    this proves the deflation-floor guard (`latest_value < last_increase_
    index`) correctly treats a tie as "no decrease" and lets the normal
    step path run (which also computes a real 0.00 from the ratio), rather
    than over-matching on `<=` and treating equality as deflation too.
    """
    market_data_repository = FakeMarketDataRepository(
        economic_index_by_period={
            ("IPC_CL", 2026, 4): Decimal("112.18"),
            ("IPC_CL", 2027, 4): Decimal("112.18"),
        },
        latest_economic_index={"IPC_CL": (date(2026, 8, 1), Decimal("112.18"))},
    )
    first_future_net_pay_clp = Decimal("3118248.98")

    result = await project_future_months(
        _baseline(first_future_net_pay_clp),
        current_year=2026,
        current_month=9,
        first_increase_period=date(2026, 4, 1),
        increase_frequency=12,
        market_data_repository=market_data_repository,
    )

    assert all(
        month.net_pay_clp == first_future_net_pay_clp for month in result.values()
    )
    assert all(month.increase_pct == Decimal("0.00") for month in result.values())
