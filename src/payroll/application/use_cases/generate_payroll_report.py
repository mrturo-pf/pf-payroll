"""Use case for generating a payroll report PDF."""

from payroll.application.dto import GeneratedPayrollReportDTO
from payroll.application.ports.report_renderer import PayrollReportRenderer
from payroll.application.ports.repositories import PayrollRepository


class GeneratePayrollReport:
    """Generates a PDF payroll report for a reviewed period."""

    def __init__(
        self,
        repository: PayrollRepository,
        renderer: PayrollReportRenderer,
    ) -> None:
        self._repository = repository
        self._renderer = renderer

    async def execute(self, period_id: int) -> GeneratedPayrollReportDTO:
        detail = await self._repository.get_period_detail(period_id)
        if detail is None:
            raise ValueError(f"Payroll period {period_id} was not found.")
        if detail.status != "reviewed":
            raise ValueError(f"Payroll period {period_id} must be reviewed before generating a report.")
        if detail.summary is None:
            raise ValueError(f"Payroll summary for period {period_id} was not found.")

        return GeneratedPayrollReportDTO(
            period_id=detail.id,
            filename=f"payroll-period-{detail.id}.pdf",
            content=self._renderer.render_payroll_period(detail),
        )
