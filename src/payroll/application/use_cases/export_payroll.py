"""Use case for exporting persisted payroll periods back into spreadsheet form.

Mirror-image of ImportPayroll (application/use_cases/import_payroll.py): that
one turns file bytes into persisted rows, this one turns persisted periods
back into file bytes.
"""

from payroll.application.dto import ExportPayrollFiltersDTO, PayrollPeriodDetailDTO
from payroll.application.ports.repositories import PayrollRepository
from payroll.application.ports.spreadsheet_exporter import PayrollSpreadsheetExporter


class ExportPayroll:
    """Reads persisted payroll periods via PayrollRepository and serializes them."""

    def __init__(
        self, repository: PayrollRepository, exporter: PayrollSpreadsheetExporter
    ) -> None:
        """Initialize the instance."""
        self._repository = repository
        self._exporter = exporter

    async def execute(self, filters: ExportPayrollFiltersDTO) -> bytes:
        """Export every persisted period matching the given filters.

        Always bulk, even when the filters happen to match exactly one
        period or none at all -- a filter matching zero periods still
        produces a valid, header-only file via the exporter, never an
        error (see PayrollPeriodDetailDTO's own no-match precedent in
        list_period_summaries()).
        """
        periods: list[
            PayrollPeriodDetailDTO
        ] = await self._repository.list_period_details(filters)
        return self._exporter.export(periods)
