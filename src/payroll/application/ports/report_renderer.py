"""Port definitions for report rendering."""

from typing import Protocol

from payroll.application.dto import PayrollPeriodDetailDTO


class PayrollReportRenderer(Protocol):
    """Renders a payroll period detail into a binary report artifact."""

    def render_payroll_period(self, detail: PayrollPeriodDetailDTO) -> bytes:
        """Render payroll period."""
        ...
