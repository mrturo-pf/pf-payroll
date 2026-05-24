"""Tests for test assign plans."""

from datetime import date

import pytest

from payroll.application.dto import AssignPlansCommandDTO, AssignPlansResultDTO
from payroll.application.use_cases.assign_plans import AssignPlans


class StubPayrollRepository:
    """Test double for Payroll Repository."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.command: AssignPlansCommandDTO | None = None

    async def assign_plans(self, command: AssignPlansCommandDTO) -> AssignPlansResultDTO:
        """Assign plans."""
        self.command = command
        return AssignPlansResultDTO(
            period_id=command.period_id,
            payment_date=date(2026, 1, 31),
            pension_plan_id=command.pension_plan_id,
            health_plan_id=command.health_plan_id,
        )


@pytest.mark.asyncio
async def test_assign_plans_returns_repository_result() -> None:
    """Test assign plans returns repository result."""
    repository = StubPayrollRepository()

    result = await AssignPlans(repository).execute(
        AssignPlansCommandDTO(period_id=10, pension_plan_id=1, health_plan_id=2)
    )

    assert repository.command == AssignPlansCommandDTO(period_id=10, pension_plan_id=1, health_plan_id=2)
    assert result == AssignPlansResultDTO(
        period_id=10,
        payment_date=date(2026, 1, 31),
        pension_plan_id=1,
        health_plan_id=2,
    )
