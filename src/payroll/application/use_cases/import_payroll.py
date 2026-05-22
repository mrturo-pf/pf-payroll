"""Use case for importing payroll data."""

from io import BytesIO

from payroll.application.dto import ImportPayrollResultDTO, ImportPayrollRowDTO
from payroll.application.ports.repositories import PayrollRepository
from payroll.infrastructure.importers.xlsx_importer import read_payroll_dataframe, to_long_format


class ImportPayroll:
    """Imports payroll data from CSV/XLSX flat files into the application store."""

    def __init__(self, repository: PayrollRepository) -> None:
        self._repository = repository

    async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
        dataframe = read_payroll_dataframe(filename, BytesIO(content))
        normalized = to_long_format(dataframe)

        if normalized.empty:
            raise ValueError("The provided payroll file did not yield any importable rows.")

        rows = [
            ImportPayrollRowDTO(
                employer=str(row["employer"]),
                period_year=int(row["year"]),
                period_month=int(row["month"]),
                payment_date=row["payment_date"],
                status=row["status"],
                concept_code=str(row["concept_code"]),
                amount_clp=row["amount_clp"],
            )
            for row in normalized.to_dict(orient="records")
        ]
        return await self._repository.import_rows(rows)
