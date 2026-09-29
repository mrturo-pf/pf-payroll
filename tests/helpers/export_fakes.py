"""Shared test doubles for the payroll spreadsheet export feature.

Both the ExportPayroll use case unit tests and the GET /payroll/spreadsheet
route integration tests need the exact same thing: a PayrollRepository
double that records which ExportPayrollFiltersDTO it was called with and
returns a canned list of periods. One shared double here instead of two
independently-maintained copies.
"""

from __future__ import annotations

from payroll.application.dto import ExportPayrollFiltersDTO, PayrollPeriodDetailDTO


class FakePayrollRepository:
    """Test double recording the filters it was called with."""

    def __init__(self, periods: list[PayrollPeriodDetailDTO]) -> None:
        """Initialize the instance."""
        self.periods = periods
        self.received_filters: ExportPayrollFiltersDTO | None = None

    async def list_period_details(
        self, filters: ExportPayrollFiltersDTO
    ) -> list[PayrollPeriodDetailDTO]:
        """Record filters and return the canned periods."""
        self.received_filters = filters
        return self.periods
