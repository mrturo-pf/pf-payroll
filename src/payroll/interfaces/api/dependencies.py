"""FastAPI dependency wiring."""

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.ports.income_tax_bracket import IncomeTaxBracketPort
from payroll.application.ports.repositories import (
    ComplementaryInsuranceRepository,
    MarketDataRepository,
    PayrollRepository,
    ReferenceDataRepository,
)
from payroll.infrastructure.http.financial_data_client import FinancialDataClient
from payroll.infrastructure.http.income_tax_bracket_client import IncomeTaxBracketClient
from payroll.infrastructure.reporting.weasyprint_payroll_report_renderer import (
    WeasyPrintPayrollReportRenderer,
)
from payroll.infrastructure.importers.xlsx_importer import XlsxPayrollImporter
from payroll.application.use_cases.assign_plans import AssignPlans
from payroll.application.use_cases.compute_contributions import ComputeContributions
from payroll.application.use_cases.deflate_amounts import DeflateAmounts
from payroll.application.use_cases.generate_payroll_report import GeneratePayrollReport
from payroll.application.use_cases.compute_income_tax import ComputeIncomeTax
from payroll.application.use_cases.process_imported_payroll_periods import (
    ProcessImportedPayrollPeriods,
)
from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.application.use_cases.payroll_queries import PayrollQueries
from payroll.application.use_cases.review_payroll_period import ReviewPayrollPeriod
from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.interfaces.repositories import (
    SqlAlchemyComplementaryInsuranceRepository,
    SqlAlchemyPayrollRepository,
    SqlAlchemyReferenceDataRepository,
)
from payroll.config import settings
from payroll.interfaces.session import SessionLocal, open_session


async def get_session() -> AsyncIterator[AsyncSession]:
    """Get session."""
    async with open_session(SessionLocal) as session:
        yield session


def get_reference_data_repository(
    session: AsyncSession = Depends(get_session),
) -> ReferenceDataRepository:
    """Get reference data repository."""
    return SqlAlchemyReferenceDataRepository(session)


def get_reference_data_queries(
    repository: ReferenceDataRepository = Depends(get_reference_data_repository),
) -> ReferenceDataQueries:
    """Get reference data queries."""
    return ReferenceDataQueries(repository)


def get_payroll_repository(
    session: AsyncSession = Depends(get_session),
) -> PayrollRepository:
    """Get payroll repository."""
    return SqlAlchemyPayrollRepository(session)


def get_market_data_repository() -> MarketDataRepository:
    """Get market data repository (HTTP adapter backed by pf-rates)."""
    return FinancialDataClient(
        base_url=settings.financial_data_base_url,
        api_key=settings.financial_data_api_key,
        cache_ttl_seconds=settings.financial_data_cache_ttl_seconds,
    )


def get_income_tax_bracket_client() -> IncomeTaxBracketPort:
    """Get income tax bracket client (HTTP adapter backed by pf-rates)."""
    return IncomeTaxBracketClient(
        base_url=settings.financial_data_base_url,
        api_key=settings.financial_data_api_key,
        cache_ttl_seconds=settings.financial_data_cache_ttl_seconds,
    )


def get_complementary_insurance_repository(
    session: AsyncSession = Depends(get_session),
) -> ComplementaryInsuranceRepository:
    """Get complementary insurance repository."""
    return SqlAlchemyComplementaryInsuranceRepository(session)


def get_import_payroll_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> ImportPayroll:
    """Get import payroll use case."""
    return ImportPayroll(repository, XlsxPayrollImporter())


def get_process_imported_payroll_periods_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
    complementary_insurance_repository: ComplementaryInsuranceRepository = Depends(
        get_complementary_insurance_repository
    ),
) -> ProcessImportedPayrollPeriods:
    """Get imported-payroll post-processing use case."""
    return ProcessImportedPayrollPeriods(
        repository,
        get_market_data_repository(),
        complementary_insurance_repository,
        get_income_tax_bracket_client(),
    )


def get_payroll_queries(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> PayrollQueries:
    """Get payroll queries."""
    return PayrollQueries(repository)


def get_generate_payroll_report_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> GeneratePayrollReport:
    """Get generate payroll report use case."""
    return GeneratePayrollReport(repository, WeasyPrintPayrollReportRenderer())


def get_assign_plans_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> AssignPlans:
    """Get assign plans use case."""
    return AssignPlans(repository)


def get_review_payroll_period_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> ReviewPayrollPeriod:
    """Get review payroll period use case."""
    return ReviewPayrollPeriod(repository)


def get_compute_contributions_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> ComputeContributions:
    """Get compute contributions use case."""
    return ComputeContributions(repository, get_market_data_repository())


def get_compute_income_tax_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> ComputeIncomeTax:
    """Get compute income tax use case."""
    return ComputeIncomeTax(
        repository,
        get_market_data_repository(),
        get_income_tax_bracket_client(),
    )


def get_deflate_amounts_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> DeflateAmounts:
    """Get deflate amounts use case."""
    return DeflateAmounts(repository, get_market_data_repository())
