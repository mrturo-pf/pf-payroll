"""Use case for importing payroll data."""

from payroll.application.errors import PayrollValidationError
from payroll.application.dto import ImportPayrollResultDTO
from payroll.application.ports.importers import PayrollImporter
from payroll.application.ports.repositories import PayrollRepository


class ImportPayroll:
    """Imports payroll data from CSV/XLSX flat files into the application store."""

    def __init__(self, repository: PayrollRepository, importer: PayrollImporter) -> None:
        """Initialize the instance."""
        self._repository = repository
        self._importer = importer

    async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
        """Create from bytes."""
        rows = self._importer.read_rows(filename, content)
        if not rows:
            raise PayrollValidationError("The provided payroll file did not yield any importable rows.")
        return await self._repository.import_rows(rows)
