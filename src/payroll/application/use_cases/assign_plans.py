"""Use case for assigning pension and health plans to a payroll period."""

from payroll.application.dto import AssignPlansCommandDTO, AssignPlansResultDTO
from payroll.application.ports.repositories import PayrollRepository


class AssignPlans:
    """Assigns plan snapshot ids to an existing payroll period."""

    def __init__(self, repository: PayrollRepository) -> None:
        """Initialize the instance."""
        self._repository = repository

    async def execute(self, command: AssignPlansCommandDTO) -> AssignPlansResultDTO:
        """Handle execute."""
        return await self._repository.assign_plans(command)
