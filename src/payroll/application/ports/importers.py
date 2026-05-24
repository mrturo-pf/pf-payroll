"""Port definitions for payroll import adapters."""

from typing import Protocol

from payroll.application.dto import ImportPayrollRowDTO


class PayrollImporter(Protocol):
    """Reads external payroll files into application-level DTO rows."""

    def read_rows(self, filename: str, content: bytes) -> list[ImportPayrollRowDTO]:
        """Read rows."""
        ...
