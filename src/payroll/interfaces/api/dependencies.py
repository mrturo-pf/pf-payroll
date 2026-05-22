"""FastAPI dependency wiring."""

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.ports.repositories import MarketDataRepository, PayrollRepository, ReferenceDataRepository
from payroll.application.use_cases.market_data import MarketDataQueries
from payroll.application.use_cases.assign_plans import AssignPlans
from payroll.application.use_cases.compute_contributions import ComputeContributions
from payroll.application.use_cases.deflate_amounts import DeflateAmounts
from payroll.application.use_cases.compute_income_tax import ComputeIncomeTax
from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.application.use_cases.payroll_queries import PayrollQueries
from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.application.use_cases.refresh_rates import RefreshRates
from payroll.infrastructure.db.repositories.market_data_repository import SqlAlchemyMarketDataRepository
from payroll.infrastructure.db.repositories.payroll_repository import SqlAlchemyPayrollRepository
from payroll.infrastructure.db.repositories.reference_data_repository import SqlAlchemyReferenceDataRepository
from payroll.infrastructure.db.session import SessionLocal


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def get_reference_data_repository(
    session: AsyncSession = Depends(get_session),
) -> ReferenceDataRepository:
    return SqlAlchemyReferenceDataRepository(session)


def get_reference_data_queries(
    repository: ReferenceDataRepository = Depends(get_reference_data_repository),
) -> ReferenceDataQueries:
    return ReferenceDataQueries(repository)


def get_payroll_repository(
    session: AsyncSession = Depends(get_session),
) -> PayrollRepository:
    return SqlAlchemyPayrollRepository(session)


def get_market_data_repository(
    session: AsyncSession = Depends(get_session),
) -> MarketDataRepository:
    return SqlAlchemyMarketDataRepository(session)


def get_market_data_queries(
    repository: MarketDataRepository = Depends(get_market_data_repository),
) -> MarketDataQueries:
    return MarketDataQueries(repository)


def get_refresh_rates_use_case(
    repository: MarketDataRepository = Depends(get_market_data_repository),
) -> RefreshRates:
    return RefreshRates(repository)


def get_import_payroll_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> ImportPayroll:
    return ImportPayroll(repository)


def get_payroll_queries(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> PayrollQueries:
    return PayrollQueries(repository)


def get_assign_plans_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> AssignPlans:
    return AssignPlans(repository)


def get_compute_contributions_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
    market_data_repository: MarketDataRepository = Depends(get_market_data_repository),
) -> ComputeContributions:
    return ComputeContributions(repository, market_data_repository)


def get_compute_income_tax_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
    market_data_repository: MarketDataRepository = Depends(get_market_data_repository),
) -> ComputeIncomeTax:
    return ComputeIncomeTax(repository, market_data_repository)


def get_deflate_amounts_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
    market_data_repository: MarketDataRepository = Depends(get_market_data_repository),
) -> DeflateAmounts:
    return DeflateAmounts(repository, market_data_repository)
