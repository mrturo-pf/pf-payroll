"""Port definitions for payroll spreadsheet export adapters.

The mirror-image port of PayrollImporter (application/ports/importers.py):
that one reads external CSV/XLSX bytes into application DTOs, this one
serializes application DTOs (or nothing at all, for the blank template)
back into CSV/XLSX bytes. Concrete adapters live in
infrastructure/exporters/, next to XlsxPayrollImporter.
"""

from typing import Protocol

from payroll.application.dto import PayrollPeriodDetailDTO


class PayrollSpreadsheetExporter(Protocol):
    """Serialize persisted payroll periods, or a blank shell, into spreadsheet bytes."""

    def export(self, periods: list[PayrollPeriodDetailDTO]) -> bytes:
        """Serialize the given periods into wide-format spreadsheet bytes."""
        ...

    def export_template(self) -> bytes:
        """Serialize a blank, header-only spreadsheet shell.

        Never reads a period -- a static shell derived purely from the
        shared column shape (see wide_columns()), independently shippable
        of the real export.
        """
        ...
