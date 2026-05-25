"""Read-only payroll queries."""

from dataclasses import dataclass

from payroll.application.errors import PayrollPeriodNotFoundError
from datetime import date

from payroll.application.dto import (
    PayrollPeriodDetailDTO,
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
        self, *, today: date | None = None
    ) -> list[PayrollPeriodRangeDTO]:
        """List payroll period date ranges around the current period."""
        return await self.repository.list_period_ranges(today=today)
