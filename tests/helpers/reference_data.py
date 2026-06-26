"""Shared stub mixin for reference-data test doubles."""

from datetime import date
from decimal import Decimal

from payroll.application.dto import (
    ContributionCapDTO,
    CurrencyDTO,
    EconomicIndexDTO,
    ExchangeRateDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    IncomeTaxBracketDTO,
    PayrollConceptDTO,
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
)
from payroll.domain.contributions import EmploymentContractKind, HealthInstitutionKind


def sample_exchange_rate_dto() -> ExchangeRateDTO:
    """Return a standard UF exchange rate DTO for testing."""
    return ExchangeRateDTO(
        currency_code="UF",
        rate_date=date(2026, 1, 31),
        value_clp=Decimal("38000"),
        source="manual",
    )


def sample_economic_index_dto() -> EconomicIndexDTO:
    """Return a standard IPC_CL economic index DTO for testing."""
    return EconomicIndexDTO(
        code="IPC_CL",
        period_year=2026,
        period_month=1,
        index_value=Decimal("112.340000"),
        monthly_change=Decimal("0.7000"),
        yearly_change=Decimal("4.1000"),
        base_period="DIC-2018",
        source="manual",
    )


def sample_payroll_period_detail_dto(
    period_id: int = 1,
    *,
    employer_name: str = "ACME",
    employer_tax_id: str | None = None,
    employer_ended_at: date | None = None,
    status: str = "actual",
    items: list[PayrollItemDetailDTO] | None = None,
    summary: PayrollSummaryDTO | None = None,
    health_institution_is_active: bool | None = None,
) -> PayrollPeriodDetailDTO:
    """Return an ACME January-2026 PayrollPeriodDetailDTO for testing.

    Structural fields are fixed; callers supply only the fields that vary
    per test (period_id, status, items, employer_*, summary, etc.).
    """
    return PayrollPeriodDetailDTO(
        id=period_id,
        employer_id=1,
        employer_name=employer_name,
        employer_tax_id=employer_tax_id,
        employer_country_code="CL",
        employer_started_at=date(2020, 1, 1),
        employer_ended_at=employer_ended_at,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        worked_days=30,
        status=status,
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
        pension_plan_id=1,
        health_plan_id=2,
        items=items if items is not None else [],
        summary=summary
        if summary is not None
        else sample_payroll_summary_dto(period_id),
        health_institution_is_active=health_institution_is_active,
    )


def sample_acme_april_2026_period_detail_dto(
    *,
    status: str = "actual",
    items: list[PayrollItemDetailDTO] | None = None,
    pension_plan_id: int | None = None,
    health_plan_id: int | None = None,
    summary: PayrollSummaryDTO | None = None,
    health_institution_is_active: bool | None = None,
) -> PayrollPeriodDetailDTO:
    """Return an ACME April-2026 period detail for process/cli/dashboard tests.

    Structural fields (id=7, employer_tax_id, employer_country_code, dates,
    worked_days, employment_contract_kind) are fixed; callers supply the
    fields that vary (status, items, plan ids, summary, etc.).
    """
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
        status=status,
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
        pension_plan_id=pension_plan_id,
        health_plan_id=health_plan_id,
        items=items if items is not None else [],
        summary=summary
        if summary is not None
        else sample_acme_april_2026_summary_dto(),
        health_institution_is_active=health_institution_is_active,
    )


def sample_acme_april_2026_summary_dto() -> PayrollSummaryDTO:
    """Return a base ACME April-2026 summary DTO for testing.

    Provides the twelve structural fields shared across
    test_process_imported_payroll_periods, test_cli_main, and
    test_dashboard_app.  Call-site code adds divergent optional
    fields (declared_net_pay_clp, expected_net_pay_clp, etc.) via
    dataclasses.replace().
    """
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
    )


def sample_payroll_summary_dto(period_id: int = 1) -> PayrollSummaryDTO:
    """Return a standard ACME payroll summary DTO for testing.

    All financial values correspond to a January-2026 ACME payroll period
    with a 1 000 000 CLP taxable income and 830 000 CLP net pay.
    """
    return PayrollSummaryDTO(
        period_id=period_id,
        employer_id=1,
        employer_name="ACME",
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1000000"),
        total_discounts_clp=Decimal("170000"),
        net_pay_clp=Decimal("830000"),
    )


class ReferenceDataStubMixin:
    """Mixin that implements all eight reference-data query methods with canned data.

    Inherit from this class in test doubles to avoid duplicating the stub
    implementations across integration and unit test modules.
    """

    def __init__(self) -> None:
        """Initialize the instance."""
        self.include_inactive_health_institutions = False
        self.include_inactive_health_plans = False

    async def list_currencies(self) -> list[CurrencyDTO]:
        """List currencies."""
        return [
            CurrencyDTO(
                code="CLP", name="Peso chileno", is_fiat=True, unit_kind="currency"
            )
        ]

    async def list_pension_institutions(self) -> list[PensionInstitutionDTO]:
        """List pension institutions."""
        return [
            PensionInstitutionDTO(
                code="AFP_UNO",
                name="AFP Uno",
                mandatory_rate=Decimal("0.10"),
                is_active=True,
            )
        ]

    async def list_health_institutions(
        self, *, include_inactive: bool = False
    ) -> list[HealthInstitutionDTO]:
        """List health institutions."""
        self.include_inactive_health_institutions = include_inactive
        return [
            HealthInstitutionDTO(
                code="FONASA",
                name="Fonasa",
                kind=HealthInstitutionKind.FONASA,
                mandatory_rate=Decimal("0.07"),
                is_active=True,
            )
        ]

    async def list_pension_plans(self) -> list[PensionPlanDTO]:
        """List pension plans."""
        return [
            PensionPlanDTO(
                id=1,
                institution_code="AFP_UNO",
                institution_name="AFP Uno",
                valid_from=date(2024, 1, 1),
                valid_to=None,
                additional_rate=Decimal("0"),
            )
        ]

    async def list_health_plans(
        self, *, include_inactive: bool = False
    ) -> list[HealthPlanDTO]:
        """List health plans."""
        self.include_inactive_health_plans = include_inactive
        return [
            HealthPlanDTO(
                id=2,
                institution_code="FONASA",
                institution_name="Fonasa",
                institution_kind=HealthInstitutionKind.FONASA,
                valid_from=date(2024, 1, 1),
                valid_to=None,
                plan_name="Base",
                contracted_uf=Decimal("0"),
            )
        ]

    async def list_contribution_caps(self) -> list[ContributionCapDTO]:
        """List contribution caps."""
        return [
            ContributionCapDTO(
                cap_type="pension_health",
                valid_from=date(2026, 1, 1),
                valid_to=None,
                value_uf=Decimal("90.0600"),
            )
        ]

    async def list_payroll_concepts(self) -> list[PayrollConceptDTO]:
        """List payroll concepts."""
        return [
            PayrollConceptDTO(
                code="SALARY_BASE",
                name="Base Salary",
                kind="income",
                is_taxable=True,
            )
        ]

    async def list_income_tax_brackets(self) -> list[IncomeTaxBracketDTO]:
        """List income tax brackets."""
        return [
            IncomeTaxBracketDTO(
                valid_from=date(2026, 1, 1),
                valid_to=None,
                lower_bound_utm=Decimal("0"),
                upper_bound_utm=Decimal("13.5"),
                marginal_rate=Decimal("0"),
                rebate_utm=Decimal("0"),
            )
        ]
