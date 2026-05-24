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
from payroll.application.use_cases.process_imported_payroll_periods import (
    ProcessImportedPayrollPeriods,
)
from payroll.domain.contributions import EmploymentContractKind


def _sample_summary() -> PayrollSummaryDTO:
    """Build a sample summary."""
    return PayrollSummaryDTO(
        period_id=7,
        employer_id=1,
        employer_name="ACME",
        period_year=2026,
        period_month=4,
        payment_date=date(2026, 4, 30),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1250000"),
        total_discounts_clp=Decimal("180000"),
        net_pay_clp=Decimal("1070000"),
        declared_net_pay_clp=Decimal("1050000"),
    )


def _sample_detail(
    *, item_codes: list[str], expected_net_pay_clp: Decimal | None = None
) -> PayrollPeriodDetailDTO:
    """Build a sample period detail."""
    items = [
        PayrollItemDetailDTO(
            concept_code=code,
            concept_name=code.title(),
            kind="discount" if code != "SALARY_BASE" else "income",
            is_taxable=code == "SALARY_BASE",
            amount_clp=Decimal("1000"),
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
    return PayrollPeriodDetailDTO(
        id=7,
        employer_id=1,
        employer_name="ACME",
        employer_tax_id="76000000-1",
        employer_country_code="CL",
        employer_started_at=date(2020, 1, 1),
        employer_ended_at=None,
        period_year=2026,
        period_month=4,
        payment_date=date(2026, 4, 30),
        worked_days=30,
        status="actual",
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
        pension_plan_id=None,
        health_plan_id=None,
        health_institution_is_active=None,
        items=items,
        summary=summary,
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

    async def get_exchange_rate_value(
        self, currency_code: str, rate_date: date
    ) -> Decimal | None:
        """Get exchange rate value."""
        if currency_code == "UF":
            return Decimal("40000")
        return Decimal("70000")


@pytest.mark.asyncio
async def test_process_imported_payroll_periods_compute_and_refresh() -> None:
    """Test post-processing computes missing derived items and refreshes periods."""
    repository = StubPayrollRepository()
    use_case = ProcessImportedPayrollPeriods(
        repository,
        StubMarketDataRepository(),  # type: ignore[arg-type]
    )

    result = await use_case.execute(
        ImportPayrollResultDTO(
            imported_periods=1,
            imported_items=5,
            periods=[
                ImportedPayrollPeriodDTO(
                    id=7,
                    employer="ACME",
                    period_year=2026,
                    period_month=4,
                    payment_date=date(2026, 4, 30),
                    status="actual",
                    employment_contract_kind=EmploymentContractKind.INDEFINITE,
                    item_count=5,
                    declared_net_pay_clp=Decimal("1050000"),
                )
            ],
        )
    )

    assert repository.saved_unemployment == [7]
    assert repository.saved_tax == [7]
    assert result.periods[0].item_count == 7
    assert result.periods[0].expected_net_pay_clp == Decimal("1100000")


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

    original_period = ImportedPayrollPeriodDTO(
        id=7,
        employer="ACME",
        period_year=2026,
        period_month=4,
        payment_date=date(2026, 4, 30),
        status="actual",
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
        item_count=5,
    )
    result = await ProcessImportedPayrollPeriods(
        MissingRefreshRepository(),
        StubMarketDataRepository(),  # type: ignore[arg-type]
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

    original_period = ImportedPayrollPeriodDTO(
        id=7,
        employer="ACME",
        period_year=2026,
        period_month=4,
        payment_date=date(2026, 4, 30),
        status="actual",
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
        item_count=5,
    )
    result = await ProcessImportedPayrollPeriods(
        MissingSummaryRepository(),
        StubMarketDataRepository(),  # type: ignore[arg-type]
    ).execute(
        ImportPayrollResultDTO(
            imported_periods=1,
            imported_items=5,
            periods=[original_period],
        )
    )

    assert result.periods[0] == original_period
