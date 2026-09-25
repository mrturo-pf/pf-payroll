"""Use case for importing payroll data."""

from payroll.application.errors import PayrollValidationError
from payroll.application.dto import ImportPayrollResultDTO, ImportPayrollRowDTO
from payroll.application.ports.importers import PayrollImporter
from payroll.application.ports.repositories import PayrollRepository


class ImportPayroll:
    """Imports payroll data from CSV/XLSX flat files into the application store."""

    def __init__(
        self, repository: PayrollRepository, importer: PayrollImporter
    ) -> None:
        """Initialize the instance."""
        self._repository = repository
        self._importer = importer

    async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
        """Create from bytes."""
        rows = self._importer.read_rows(filename, content)
        if not rows:
            raise PayrollValidationError(
                "The provided payroll file did not yield any importable rows."
            )
        return await self._repository.import_rows(rows)

    async def from_rows(
        self, rows: list[ImportPayrollRowDTO]
    ) -> ImportPayrollResultDTO:
        """Import already-structured payroll rows, skipping the file-parsing step.

        Sibling to from_bytes(): used by POST /payroll/import/rows, where rows
        come from a previously-previewed-and-edited PDF import instead of a
        parsed CSV/XLSX file. Delegates to the exact same
        PayrollRepository.import_rows() -- no separate persistence path.
        """
        if not rows:
            raise PayrollValidationError(
                "The provided payroll rows list must not be empty."
            )
        return await self._repository.import_rows(rows)
