"""Use case for marking a payroll period as reviewed."""

from payroll.application.dto import (
    ReviewPayrollPeriodCommandDTO,
    ReviewPayrollPeriodResultDTO,
)
from payroll.application.ports.repositories import PayrollRepository


class ReviewPayrollPeriod:
    """Marks a payroll period as reviewed once all mandatory calculations exist."""

    def __init__(self, repository: PayrollRepository) -> None:
        """Initialize the instance."""
        self._repository = repository

    async def execute(
        self, command: ReviewPayrollPeriodCommandDTO
    ) -> ReviewPayrollPeriodResultDTO:
        """Handle execute."""
        return await self._repository.review_period(command)
