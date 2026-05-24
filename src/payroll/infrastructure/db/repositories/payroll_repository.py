"""SQLAlchemy payroll repository facade composed from focused repository concerns."""

from payroll.infrastructure.db.repositories.payroll_repository_commands import SqlAlchemyPayrollCommandRepository
from payroll.infrastructure.db.repositories.payroll_repository_imports import SqlAlchemyPayrollImportRepository
from payroll.infrastructure.db.repositories.payroll_repository_queries import SqlAlchemyPayrollQueryRepository


class SqlAlchemyPayrollRepository(
    SqlAlchemyPayrollImportRepository,
    SqlAlchemyPayrollCommandRepository,
    SqlAlchemyPayrollQueryRepository,
):
    """Repository facade keeping the public import path stable."""
