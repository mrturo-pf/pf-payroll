"""Read-only payroll queries."""

from dataclasses import dataclass

from payroll.application.errors import PayrollPeriodNotFoundError
from payroll.application.dto import PayrollPeriodDetailDTO, PayrollSummaryDTO
from payroll.application.ports.repositories import PayrollRepository


@dataclass(slots=True)
class PayrollQueries:
    repository: PayrollRepository

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO:
        detail = await self.repository.get_period_detail(period_id)
        if detail is None:
            raise PayrollPeriodNotFoundError(f"Payroll period {period_id} was not found.")
        return detail

    async def list_period_summaries(self) -> list[PayrollSummaryDTO]:
        return await self.repository.list_period_summaries()
