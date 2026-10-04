"""Use case for transactional payroll-period deletion."""

from payroll.application.errors import PayrollValidationError
from payroll.application.ports.repositories import PayrollRepository


class DeletePayrollPeriods:
    """Delete one or more payroll periods as one atomic operation."""

    def __init__(self, repository: PayrollRepository) -> None:
        """Initialize the use case."""
        self._repository = repository

    async def execute(self, period_ids: list[int]) -> None:
        """Validate IDs and delete every requested period."""
        if not period_ids:
            raise PayrollValidationError("period_ids must not be empty.")
        if len(period_ids) > 500:
            raise PayrollValidationError("A maximum of 500 period_ids is allowed.")
        if any(period_id <= 0 for period_id in period_ids):
            raise PayrollValidationError("Every period_id must be positive.")
        if len(set(period_ids)) != len(period_ids):
            raise PayrollValidationError("period_ids must not contain duplicates.")
        await self._repository.delete_periods(period_ids)
