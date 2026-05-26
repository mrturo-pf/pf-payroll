"""Use case for computing contributions."""

from payroll.application.dto import (
    ComputeContributionsCommandDTO,
    ComputeContributionsResultDTO,
)
from payroll.application.ports.repositories import (
    MarketDataRepository,
    PayrollRepository,
)
from payroll.application.services.contribution_computation import (
    ContributionComputationService,
)


class ComputeContributions:
    """Computes pension and health contributions for an imported payroll period."""

    def __init__(
        self,
        repository: PayrollRepository,
        market_data_repository: MarketDataRepository,
    ) -> None:
        """Initialize the instance."""
        self._repository = repository
        self._service = ContributionComputationService(
            repository, market_data_repository
        )

    async def execute(
        self, command: ComputeContributionsCommandDTO
    ) -> ComputeContributionsResultDTO:
        """Handle execute."""
        result = await self._service.compute(command)
        return await self._repository.save_computed_contributions(result)
