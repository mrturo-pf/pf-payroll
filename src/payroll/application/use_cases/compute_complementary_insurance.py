"""Use case for computing complementary insurance costs."""

from payroll.application.dto import ComputeComplementaryInsuranceResultDTO
from payroll.application.ports.repositories import (
    ComplementaryInsuranceRepository,
    PayrollRepository,
)
from payroll.application.services.complementary_insurance_cost_computation import (
    ComplementaryInsuranceCostComputationService,
)


class ComputeComplementaryInsurance:
    """Computes complementary insurance costs for an imported payroll period."""

    def __init__(
        self,
        repository: PayrollRepository,
        complementary_insurance_repository: ComplementaryInsuranceRepository,
    ) -> None:
        """Initialize the instance."""
        self._repository = repository
        self._service = ComplementaryInsuranceCostComputationService(
            repository, complementary_insurance_repository
        )

    async def execute(self, period_id: int) -> ComputeComplementaryInsuranceResultDTO:
        """Handle execute."""
        return await self._service.compute(period_id)
