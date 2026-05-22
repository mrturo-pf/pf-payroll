"""FastAPI dependency wiring."""

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.ports.repositories import PayrollRepository, ReferenceDataRepository
from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.application.use_cases.reference_data import ReferenceDataQueries
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


def get_import_payroll_use_case(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> ImportPayroll:
    return ImportPayroll(repository)
