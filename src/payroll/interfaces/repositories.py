"""Re-exports of SQLAlchemy repository implementations for interface adapters."""

from payroll.infrastructure.db.repositories.complementary_insurance_repository import (
    SqlAlchemyComplementaryInsuranceRepository,
)
from payroll.infrastructure.db.repositories.payroll_repository import (
    SqlAlchemyPayrollRepository,
)
from payroll.infrastructure.db.repositories.reference_data_repository import (
    SqlAlchemyReferenceDataRepository,
)

__all__ = [
    "SqlAlchemyComplementaryInsuranceRepository",
    "SqlAlchemyPayrollRepository",
    "SqlAlchemyReferenceDataRepository",
]
