"""Read-only payroll queries."""

from dataclasses import dataclass

from payroll.application.errors import PayrollPeriodNotFoundError
from datetime import date

from payroll.application.dto import (
    PayrollPeriodDetailDTO,
    PayrollPeriodRangeContextDTO,
    PayrollPeriodRangeDTO,
    PayrollSummaryDTO,
)
from payroll.application.ports.repositories import PayrollRepository


@dataclass(slots=True)
class PayrollQueries:
    """Provide payroll queries."""

    repository: PayrollRepository

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO:
        """Get period detail."""
        detail = await self.repository.get_period_detail(period_id)
        if detail is None:
            raise PayrollPeriodNotFoundError(
                f"Payroll period {period_id} was not found."
            )
        return detail

    async def list_period_summaries(self) -> list[PayrollSummaryDTO]:
        """List period summaries."""
        return await self.repository.list_period_summaries()

    async def list_period_ranges(
        self,
        *,
        today: date | None = None,
        previous_months: int | None = None,
        future_months: int | None = None,
    ) -> list[PayrollPeriodRangeDTO]:
        """List payroll period date ranges around the current period."""
        return await self.repository.list_period_ranges(
            today=today,
            previous_months=previous_months,
            future_months=future_months,
        )

    async def get_period_range(self, period_id: int) -> PayrollPeriodRangeContextDTO:
        """Get a single real period in the unified period-range shape."""
        context = await self.repository.get_period_range(period_id)
        if context is None:
            raise PayrollPeriodNotFoundError(
                f"Payroll period {period_id} was not found."
            )
        return context
