"""FastAPI dependency wiring."""

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.ports.repositories import (
    MarketDataRepository,
    PayrollRepository,
    ReferenceDataRepository,
)
from payroll.infrastructure.rate_providers.chained_provider import (
    ChainedEconomicIndexProvider,
    ChainedFxProvider,
)
from payroll.infrastructure.rate_providers.official_providers import (
    BcchSeriesProvider,
    MindicadorRateProvider,
    SiiIncomeTaxBracketProvider,
    SiiIndicatorsProvider,
)
from payroll.infrastructure.reporting.weasyprint_payroll_report_renderer import (
    WeasyPrintPayrollReportRenderer,
)
from payroll.infrastructure.importers.xlsx_importer import XlsxPayrollImporter
from payroll.application.use_cases.market_data import MarketDataQueries
from payroll.application.use_cases.assign_plans import AssignPlans
from payroll.application.use_cases.compute_contributions import ComputeContributions
from payroll.application.use_cases.deflate_amounts import DeflateAmounts
from payroll.application.use_cases.generate_payroll_report import GeneratePayrollReport
from payroll.application.use_cases.compute_income_tax import ComputeIncomeTax
from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.application.use_cases.payroll_queries import PayrollQueries
from payroll.application.use_cases.review_payroll_period import ReviewPayrollPeriod
from payroll.application.use_cases.refresh_income_tax_brackets import (
    RefreshIncomeTaxBrackets,
)
from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.application.use_cases.refresh_rates import RefreshRates
from payroll.infrastructure.db.repositories.market_data_repository import (
    SqlAlchemyMarketDataRepository,
)
from payroll.infrastructure.db.repositories.payroll_repository import (
    SqlAlchemyPayrollRepository,
)
from payroll.infrastructure.db.repositories.reference_data_repository import (
    SqlAlchemyReferenceDataRepository,
)
from payroll.infrastructure.db.session import SessionLocal
from payroll.config import settings


async def get_session() -> AsyncIterator[AsyncSession]:
    """Get session."""
    async with SessionLocal() as session:
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


def get_income_tax_bracket_provider() -> SiiIncomeTaxBracketProvider:
    """Get income tax bracket provider."""
    return SiiIncomeTaxBracketProvider(
        base_url=settings.sii_base_url,
        timeout_seconds=settings.rate_provider_timeout_seconds,
    )


def get_refresh_income_tax_brackets_use_case(
    repository: ReferenceDataRepository = Depends(get_reference_data_repository),
) -> RefreshIncomeTaxBrackets:
    """Get refresh income tax brackets use case."""
    return RefreshIncomeTaxBrackets(repository, get_income_tax_bracket_provider())


def get_payroll_repository(
    session: AsyncSession = Depends(get_session),
) -> PayrollRepository:
    """Get payroll repository."""
    return SqlAlchemyPayrollRepository(session)


def get_market_data_repository(
    session: AsyncSession = Depends(get_session),
) -> MarketDataRepository:
    """Get market data repository."""
    return SqlAlchemyMarketDataRepository(session)


def get_market_data_queries(
    repository: MarketDataRepository = Depends(get_market_data_repository),
) -> MarketDataQueries:
    """Get market data queries."""
    return MarketDataQueries(repository)


def get_refresh_rates_use_case(
    repository: MarketDataRepository = Depends(get_market_data_repository),
) -> RefreshRates:
    """Get refresh rates use case."""
    return RefreshRates(
        repository, get_fx_rate_provider(), get_economic_index_provider()
    )


def get_fx_rate_provider() -> ChainedFxProvider:
    """Get fx rate provider."""
    bcch_provider = BcchSeriesProvider(
        user=settings.bcch_api_user,
        password=settings.bcch_api_password,
        series_codes={
            "UF": settings.bcch_series_uf,
            "USD": settings.bcch_series_usd,
            "EUR": settings.bcch_series_eur,
            "UTM": settings.bcch_series_utm,
        },
        base_url=settings.bcch_api_base_url,
        timeout_seconds=settings.rate_provider_timeout_seconds,
    )
    return ChainedFxProvider(
        [
            bcch_provider,
            SiiIndicatorsProvider(
                base_url=settings.sii_base_url,
                timeout_seconds=settings.rate_provider_timeout_seconds,
            ),
            MindicadorRateProvider(
                base_url=settings.mindicador_base_url,
                timeout_seconds=settings.rate_provider_timeout_seconds,
            ),
        ]
    )


def get_economic_index_provider() -> ChainedEconomicIndexProvider:
    """Get economic index provider."""
    return ChainedEconomicIndexProvider(
        [
            BcchSeriesProvider(
                user=settings.bcch_api_user,
                password=settings.bcch_api_password,
                series_codes={"IPC_CL": settings.bcch_series_ipc_cl},
                base_url=settings.bcch_api_base_url,
                timeout_seconds=settings.rate_provider_timeout_seconds,
            ),
            SiiIndicatorsProvider(
                base_url=settings.sii_base_url,
                timeout_seconds=settings.rate_provider_timeout_seconds,
            ),
        ]
    )


def get_import_payroll_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> ImportPayroll:
    """Get import payroll use case."""
    return ImportPayroll(repository, XlsxPayrollImporter())


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
    market_data_repository: MarketDataRepository = Depends(get_market_data_repository),
) -> ComputeContributions:
    """Get compute contributions use case."""
    return ComputeContributions(repository, market_data_repository)


def get_compute_income_tax_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
    market_data_repository: MarketDataRepository = Depends(get_market_data_repository),
) -> ComputeIncomeTax:
    """Get compute income tax use case."""
    return ComputeIncomeTax(repository, market_data_repository)


def get_deflate_amounts_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
    market_data_repository: MarketDataRepository = Depends(get_market_data_repository),
) -> DeflateAmounts:
    """Get deflate amounts use case."""
    return DeflateAmounts(repository, market_data_repository)
