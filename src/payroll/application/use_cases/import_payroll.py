"""Use case for importing payroll data."""
from io import BytesIO

from payroll.application.dto import ImportPayrollResultDTO, ImportPayrollRowDTO
from payroll.application.ports.repositories import PayrollRepository
from payroll.infrastructure.importers.xlsx_importer import (
    extract_net_pay_validations,
    read_payroll_dataframe,
    to_long_format,
)


class ImportPayroll:
    """Imports payroll data from CSV/XLSX flat files into the application store."""

    def __init__(self, repository: PayrollRepository) -> None:
        self._repository = repository

    async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
        dataframe = read_payroll_dataframe(filename, BytesIO(content))
        normalized = to_long_format(dataframe)
        net_pay_validations = extract_net_pay_validations(dataframe)

        if normalized.empty:
            raise ValueError("The provided payroll file did not yield any importable rows.")

        rows = []
        for row in normalized.to_dict(orient="records"):
            validation = net_pay_validations.get((str(row["employer"]), int(row["year"]), int(row["month"])))
            rows.append(
                ImportPayrollRowDTO(
                    employer=str(row["employer"]),
                    period_year=int(row["year"]),
                    period_month=int(row["month"]),
                    payment_date=row["payment_date"],
                    status=row["status"],
                    employment_contract_kind=row["employment_contract_kind"],
                    concept_code=str(row["concept_code"]),
                    amount_clp=row["amount_clp"],
                    declared_net_pay_clp=None if validation is None else validation.declared_net_pay_clp,
                    expected_net_pay_clp=None if validation is None else validation.expected_net_pay_clp,
                    net_pay_difference_clp=None if validation is None else validation.net_pay_difference_clp,
                )
            )
        return await self._repository.import_rows(rows)
