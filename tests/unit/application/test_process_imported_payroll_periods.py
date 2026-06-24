"""Tests for processing imported payroll periods."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    ComputeIncomeTaxResultDTO,
    ComputeUnemploymentInsuranceResultDTO,
    ImportPayrollResultDTO,
    ImportedPayrollPeriodDTO,
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)
from payroll.application.errors import EconomicIndexNotFoundError
from payroll.application.use_cases.process_imported_payroll_periods import (
    ProcessImportedPayrollPeriods,
)
from payroll.domain.contributions import (
    ComplementaryInsuranceCostType,
    EmploymentContractKind,
    HealthInstitutionKind,
)
from tests.helpers.reference_data import (
    sample_acme_april_2026_period_detail_dto,
    sample_acme_april_2026_summary_dto,
)


def _sample_summary() -> PayrollSummaryDTO:
    """Build a sample summary with declared net pay."""
    return replace(
        sample_acme_april_2026_summary_dto(),
        declared_net_pay_clp=Decimal("1050000"),
    )


def _sample_detail(
    *,
    item_codes: list[str],
    expected_net_pay_clp: Decimal | None = None,
    pension_plan_id: int | None = None,
    health_plan_id: int | None = None,
    item_amounts: dict[str, Decimal] | None = None,
) -> PayrollPeriodDetailDTO:
    """Build a sample period detail."""
    items = [
        PayrollItemDetailDTO(
            concept_code=code,
            concept_name=code.title(),
            kind="discount" if code != "SALARY_BASE" else "income",
            is_taxable=code == "SALARY_BASE",
            amount_clp=(
                item_amounts[code]
                if item_amounts is not None and code in item_amounts
                else Decimal("1000")
            ),
            notes=None,
        )
        for code in item_codes
    ]
    summary = replace(
        _sample_summary(),
        expected_net_pay_clp=expected_net_pay_clp,
        net_pay_difference_clp=(
            None if expected_net_pay_clp is None else Decimal("-50000")
        ),
        net_pay_warning=(
            None
            if expected_net_pay_clp is not None
            else (
                "Declared net_pay will be reconciled after computed contributions "
                "and income tax are generated."
            )
        ),
    )
    return sample_acme_april_2026_period_detail_dto(
        status="actual",
        pension_plan_id=pension_plan_id,
        health_plan_id=health_plan_id,
        items=items,
        summary=summary,
    )


def _default_imported_period(
    *, declared_net_pay_clp: Decimal | None = None
) -> ImportedPayrollPeriodDTO:
    return ImportedPayrollPeriodDTO(
        id=7,
        employer="ACME",
        period_year=2026,
        period_month=4,
        payment_date=date(2026, 4, 30),
        status="actual",
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
        item_count=5,
        declared_net_pay_clp=declared_net_pay_clp,
    )


def _default_import_result(
    *, declared_net_pay_clp: Decimal | None = None
) -> ImportPayrollResultDTO:
    return ImportPayrollResultDTO(
        imported_periods=1,
        imported_items=5,
        periods=[_default_imported_period(declared_net_pay_clp=declared_net_pay_clp)],
    )


class StubPayrollRepository:
    """Test double for Payroll Repository."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.detail_calls = 0
        self.saved_unemployment: list[int] = []
        self.saved_tax: list[int] = []

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO | None:
        """Get period detail."""
        self.detail_calls += 1
        if self.detail_calls == 1:
            return _sample_detail(
                item_codes=[
                    "SALARY_BASE",
                    "PENSION_BASE",
                    "PENSION_ADDITIONAL",
                    "HEALTH_BASE",
                    "HEALTH_ADDITIONAL_UF",
                ]
            )
        return _sample_detail(
            item_codes=[
                "SALARY_BASE",
                "PENSION_BASE",
                "PENSION_ADDITIONAL",
                "HEALTH_BASE",
                "HEALTH_ADDITIONAL_UF",
                "UNEMPLOYMENT_INSURANCE",
                "INCOME_TAX",
            ],
            expected_net_pay_clp=Decimal("1100000"),
        )

    async def get_unemployment_context(self, command: object) -> object:
        """Get unemployment context."""
        return type(
            "Context",
            (),
            {
                "period_id": 7,
                "payment_date": date(2026, 4, 30),
                "taxable_income_clp": Decimal("1000000"),
                "employment_contract_kind": EmploymentContractKind.INDEFINITE,
                "unemployment_cap": type(
                    "Cap",
                    (),
                    {"value_uf": Decimal("90.0000")},
                )(),
            },
        )()

    async def get_contribution_context(self, command: object) -> object:
        """Get contribution context."""
        return type(
            "Context",
            (),
            {
                "period_id": 7,
                "payment_date": date(2026, 4, 30),
                "taxable_income_clp": Decimal("1000000"),
                "employment_contract_kind": EmploymentContractKind.INDEFINITE,
                "pension_plan": type(
                    "PensionPlan",
                    (),
                    {
                        "id": 1,
                        "institution": type(
                            "PensionInstitution",
                            (),
                            {
                                "code": "AFP_UNO",
                                "name": "AFP Uno",
                                "mandatory_rate": Decimal("0.10"),
                            },
                        )(),
                        "valid_from": date(2026, 1, 1),
                        "valid_to": None,
                        "additional_rate": Decimal("0.0127"),
                    },
                )(),
                "health_plan": type(
                    "HealthPlan",
                    (),
                    {
                        "id": 2,
                        "institution": type(
                            "HealthInstitution",
                            (),
                            {
                                "code": "BANMEDICA",
                                "name": "Banmedica",
                                "kind": HealthInstitutionKind.ISAPRE,
                                "mandatory_rate": Decimal("0.07"),
                            },
                        )(),
                        "valid_from": date(2026, 1, 1),
                        "valid_to": None,
                        "plan_name": "Plan Oro",
                        "contracted_uf": Decimal("8.1000"),
                    },
                )(),
                "cap": type(
                    "Cap",
                    (),
                    {
                        "cap_type": "pension_health",
                        "valid_from": date(2026, 1, 1),
                        "valid_to": None,
                        "value_uf": Decimal("90.0000"),
                    },
                )(),
                "unemployment_cap": type(
                    "Cap",
                    (),
                    {
                        "cap_type": "unemployment",
                        "valid_from": date(2026, 1, 1),
                        "valid_to": None,
                        "value_uf": Decimal("90.0000"),
                    },
                )(),
            },
        )()

    async def save_computed_unemployment(
        self, result: ComputeUnemploymentInsuranceResultDTO
    ) -> ComputeUnemploymentInsuranceResultDTO:
        """Save computed unemployment."""
        self.saved_unemployment.append(result.period_id)
        return result

    async def get_income_tax_context(self, command: object) -> object:
        """Get income tax context."""
        return type(
            "Context",
            (),
            {
                "period_id": 7,
                "payment_date": date(2026, 4, 30),
                "taxable_income_clp": Decimal("1000000"),
                "deductible_amount_clp": Decimal("200000"),
            },
        )()

    async def get_income_tax_bracket(
        self, payment_date: date, taxable_base_utm: Decimal
    ) -> object:
        """Get income tax bracket."""
        return type(
            "Bracket",
            (),
            {
                "valid_from": date(2026, 1, 1),
                "valid_to": None,
                "lower_bound_utm": Decimal("0"),
                "upper_bound_utm": Decimal("13.5"),
                "marginal_rate": Decimal("0"),
                "rebate_utm": Decimal("0"),
            },
        )()

    async def save_computed_income_tax(
        self, result: ComputeIncomeTaxResultDTO
    ) -> ComputeIncomeTaxResultDTO:
        """Save computed income tax."""
        self.saved_tax.append(result.period_id)
        return result


class StubMarketDataRepository:
    """Test double for Market Data Repository."""

    def __init__(
        self, uf_value: Decimal | dict[date, Decimal] | None = Decimal("40000")
    ) -> None:
        """Initialize the instance."""
        self.uf_value = uf_value

    async def get_exchange_rate_value(
        self, currency_code: str, rate_date: date
    ) -> Decimal | None:
        """Get exchange rate value."""
        if currency_code == "UF":
            if isinstance(self.uf_value, dict):
                return self.uf_value.get(rate_date)
            return self.uf_value
        return Decimal("70000")


class StubComplementaryInsuranceRepository:
    """Test double for Complementary Insurance Repository."""

    async def get_vigent_plans(self, reference_date: date) -> list:  # type: ignore[no-untyped-def]
        """Get vigent plans."""
        return []

    async def assign_plans_to_period(self, period_id: int, plan_ids: list[int]) -> None:
        """Assign plans to period."""

    async def get_period_plans(self, period_id: int) -> list:  # type: ignore[no-untyped-def]
        """Get period plans."""
        return []


class FixedUfComplementaryInsuranceRepository(StubComplementaryInsuranceRepository):
    """Repository that returns a FIXED_UF plan."""

    @staticmethod
    def _fixed_uf_plan() -> object:
        """Build a FIXED_UF plan object."""
        return type(
            "Plan",
            (),
            {
                "id": 99,
                "name": "Plan UF",
                "cost_type": ComplementaryInsuranceCostType.FIXED_UF,
                "cost_value": Decimal("1.5"),
            },
        )()

    async def get_vigent_plans(self, reference_date: date) -> list:  # type: ignore[no-untyped-def]
        """Get vigent plans."""
        return [self._fixed_uf_plan()]

    async def get_period_plans(self, period_id: int) -> list:  # type: ignore[no-untyped-def]
        """Get plans assigned to the period."""
        return [self._fixed_uf_plan()]


class EconomicIndexFailingContributions:
    """Test double for a contribution service that raises a missing index error."""

    async def compute(self, command: object) -> object:
        """Raise the expected missing index error."""
        raise EconomicIndexNotFoundError(
            "UF rate not found for period 2026-04. Please load UF data before "
            "importing payroll."
        )


class FailingUfMarketDataRepository(StubMarketDataRepository):
    """Market data repository that keeps UF available for the import phase."""


class MissingUfValidationRepository(StubPayrollRepository):
    """Repository that returns imported contribution amounts for refresh validation."""

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO | None:
        """Get period detail."""
        return _sample_detail(
            item_codes=[
                "SALARY_BASE",
                "PENSION_BASE",
                "PENSION_ADDITIONAL",
                "HEALTH_BASE",
                "HEALTH_ADDITIONAL_UF",
            ],
            pension_plan_id=1,
            health_plan_id=2,
            item_amounts={
                "PENSION_BASE": Decimal("100000"),
                "PENSION_ADDITIONAL": Decimal("12700"),
                "HEALTH_BASE": Decimal("70000"),
                "HEALTH_ADDITIONAL_UF": Decimal("213500"),
            },
        )


@pytest.mark.asyncio
async def test_process_imported_payroll_periods_compute_and_refresh() -> None:
    """Test post-processing computes missing derived items and refreshes periods."""
    repository = StubPayrollRepository()
    use_case = ProcessImportedPayrollPeriods(
        repository,
        StubMarketDataRepository(),  # type: ignore[arg-type]
        StubComplementaryInsuranceRepository(),  # type: ignore[arg-type]
    )

    result = await use_case.execute(
        _default_import_result(declared_net_pay_clp=Decimal("1050000"))
    )

    assert repository.saved_unemployment == [7]
    assert repository.saved_tax == [7]
    assert result.periods[0].item_count == 7
    assert result.periods[0].expected_net_pay_clp == Decimal("1100000")


@pytest.mark.asyncio
async def test_process_imported_payroll_periods_validates_imported_contributions() -> (
    None
):
    """Test post-processing validates imported contribution breakdowns."""

    class ValidatedRepository(StubPayrollRepository):
        """Repository with plan snapshots assigned."""

        async def get_period_detail(
            self, period_id: int
        ) -> PayrollPeriodDetailDTO | None:
            """Get period detail."""
            return _sample_detail(
                item_codes=[
                    "SALARY_BASE",
                    "PENSION_BASE",
                    "PENSION_ADDITIONAL",
                    "HEALTH_BASE",
                    "HEALTH_ADDITIONAL_UF",
                ],
                pension_plan_id=1,
                health_plan_id=2,
                item_amounts={
                    "PENSION_BASE": Decimal("100000"),
                    "PENSION_ADDITIONAL": Decimal("12700"),
                    "HEALTH_BASE": Decimal("70000"),
                    "HEALTH_ADDITIONAL_UF": Decimal("213500"),
                },
            )

    repository = ValidatedRepository()
    use_case = ProcessImportedPayrollPeriods(
        repository,
        StubMarketDataRepository(Decimal("35000")),  # type: ignore[arg-type]
        StubComplementaryInsuranceRepository(),  # type: ignore[arg-type]
    )

    result = await use_case.execute(
        _default_import_result(declared_net_pay_clp=Decimal("1050000"))
    )

    assert repository.saved_unemployment == [7]
    assert repository.saved_tax == [7]
    assert result.periods[0].contribution_validation is not None
    assert result.periods[0].contribution_validation.expected_pension_base_clp == (
        Decimal("100000")
    )
    assert result.periods[
        0
    ].contribution_validation.expected_health_plan_additional_clp == Decimal("213500")
    assert result.periods[0].contribution_validation.warning is None


@pytest.mark.asyncio
async def test_process_imported_payroll_periods_skips_missing_imported_codes() -> None:
    """Test post-processing skips periods without the imported contribution base."""

    class MissingCodesRepository(StubPayrollRepository):
        """Repository missing imported contribution codes."""

        async def get_period_detail(
            self, period_id: int
        ) -> PayrollPeriodDetailDTO | None:
            """Get period detail."""
            return _sample_detail(item_codes=["SALARY_BASE", "PENSION_BASE"])

    repository = MissingCodesRepository()
    use_case = ProcessImportedPayrollPeriods(
        repository,
        StubMarketDataRepository(),  # type: ignore[arg-type]
        StubComplementaryInsuranceRepository(),  # type: ignore[arg-type]
    )

    result = await use_case.execute(
        ImportPayrollResultDTO(
            imported_periods=1,
            imported_items=2,
            periods=[
                ImportedPayrollPeriodDTO(
                    id=7,
                    employer="ACME",
                    period_year=2026,
                    period_month=4,
                    payment_date=date(2026, 4, 30),
                    status="actual",
                    employment_contract_kind=EmploymentContractKind.INDEFINITE,
                    item_count=2,
                )
            ],
        )
    )

    assert repository.saved_unemployment == []
    assert repository.saved_tax == []
    assert result.periods[0].item_count == 2


@pytest.mark.asyncio
async def test_process_imported_payroll_periods_keeps_original_on_missing_refresh() -> (
    None
):
    """Test post-processing keeps the original period without refresh data."""

    class MissingRefreshRepository(StubPayrollRepository):
        """Repository that cannot refresh the imported period."""

        async def get_period_detail(
            self, period_id: int
        ) -> PayrollPeriodDetailDTO | None:
            """Get period detail."""
            return None

    original_period = _default_imported_period()
    result = await ProcessImportedPayrollPeriods(
        MissingRefreshRepository(),
        StubMarketDataRepository(),  # type: ignore[arg-type]
        StubComplementaryInsuranceRepository(),  # type: ignore[arg-type]
    ).execute(
        ImportPayrollResultDTO(
            imported_periods=1,
            imported_items=5,
            periods=[original_period],
        )
    )

    assert result.periods[0] == original_period


@pytest.mark.asyncio
async def test_process_imported_payroll_periods_keeps_original_without_summary() -> (
    None
):
    """Test post-processing keeps the original period without summary data."""

    class MissingSummaryRepository(StubPayrollRepository):
        """Repository that returns detail without summary."""

        async def get_period_detail(
            self, period_id: int
        ) -> PayrollPeriodDetailDTO | None:
            """Get period detail."""
            detail = _sample_detail(
                item_codes=[
                    "SALARY_BASE",
                    "PENSION_BASE",
                    "PENSION_ADDITIONAL",
                    "HEALTH_BASE",
                    "HEALTH_ADDITIONAL_UF",
                ]
            )
            return replace(detail, summary=None)

    original_period = _default_imported_period()
    result = await ProcessImportedPayrollPeriods(
        MissingSummaryRepository(),
        StubMarketDataRepository(),  # type: ignore[arg-type]
        StubComplementaryInsuranceRepository(),  # type: ignore[arg-type]
    ).execute(
        ImportPayrollResultDTO(
            imported_periods=1,
            imported_items=5,
            periods=[original_period],
        )
    )

    assert result.periods[0] == original_period


@pytest.mark.asyncio
async def test_process_imported_payroll_periods_keeps_import_when_uf_missing() -> None:
    """Test post-processing keeps import result and surfaces pending UF warnings."""
    repository = StubPayrollRepository()
    result = await ProcessImportedPayrollPeriods(
        repository,
        StubMarketDataRepository(uf_value=None),  # type: ignore[arg-type]
        FixedUfComplementaryInsuranceRepository(),  # type: ignore[arg-type]
    ).execute(_default_import_result(declared_net_pay_clp=Decimal("1050000")))

    assert repository.saved_unemployment == []
    assert repository.saved_tax == []
    assert result.periods[0].complementary_insurance_validation is not None
    expected_warning = (
        "Complementary insurance validation pending: UF rate not found for period "
        "2026-04. Please load UF data before importing payroll."
    )
    assert (
        result.periods[0].complementary_insurance_validation.warnings[0]
        == expected_warning
    )


@pytest.mark.asyncio
async def test_process_imported_payroll_periods_replaces_warning_on_missing_uf() -> (
    None
):
    """Test refresh validation warning is replaced when UF is missing."""
    use_case = ProcessImportedPayrollPeriods(
        MissingUfValidationRepository(),
        FailingUfMarketDataRepository(),
        FixedUfComplementaryInsuranceRepository(),  # type: ignore[arg-type]
    )
    use_case._contributions = EconomicIndexFailingContributions()  # type: ignore[attr-defined]
    result = await use_case.execute(
        _default_import_result(declared_net_pay_clp=Decimal("1050000"))
    )

    assert result.periods[0].contribution_validation is not None
    assert result.periods[0].contribution_validation.warning == (
        "UF rate not found for period 2026-04. Please load UF data before "
        "importing payroll."
    )
