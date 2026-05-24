"""Tests for test review payroll period."""

from datetime import date

import pytest

from payroll.application.dto import ReviewPayrollPeriodCommandDTO, ReviewPayrollPeriodResultDTO
from payroll.application.use_cases.review_payroll_period import ReviewPayrollPeriod


class StubPayrollRepository:
    """Test double for Payroll Repository."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.command: ReviewPayrollPeriodCommandDTO | None = None

    async def review_period(self, command: ReviewPayrollPeriodCommandDTO) -> ReviewPayrollPeriodResultDTO:
        """Review period."""
        self.command = command
        return ReviewPayrollPeriodResultDTO(
            period_id=command.period_id,
            payment_date=date(2026, 1, 31),
            status="reviewed",
        )


@pytest.mark.asyncio
async def test_review_payroll_period_returns_repository_result() -> None:
    """Test review payroll period returns repository result."""
    repository = StubPayrollRepository()

    result = await ReviewPayrollPeriod(repository).execute(ReviewPayrollPeriodCommandDTO(period_id=10))

    assert repository.command == ReviewPayrollPeriodCommandDTO(period_id=10)
    assert result == ReviewPayrollPeriodResultDTO(
        period_id=10,
        payment_date=date(2026, 1, 31),
        status="reviewed",
    )
