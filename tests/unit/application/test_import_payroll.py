from decimal import Decimal

import pytest

from payroll.application.dto import ImportPayrollResultDTO, ImportedPayrollPeriodDTO
from payroll.application.use_cases.import_payroll import ImportPayroll


class StubPayrollRepository:
    def __init__(self) -> None:
        self.rows: list[object] = []

    async def import_rows(self, rows: list[object]) -> ImportPayrollResultDTO:
        self.rows = rows
        return ImportPayrollResultDTO(
            imported_periods=1,
            imported_items=len(rows),
            periods=[
                ImportedPayrollPeriodDTO(
                    id=1,
                    employer="ACME",
                    period_year=2026,
                    period_month=1,
                    payment_date=rows[0].payment_date,
                    status=rows[0].status,
                    item_count=len(rows),
                )
            ],
        )


@pytest.mark.asyncio
async def test_import_payroll_reads_csv_and_builds_rows() -> None:
    repository = StubPayrollRepository()
    use_case = ImportPayroll(repository)

    result = await use_case.from_bytes(
        "sample.csv",
        (
            b"period,employer,payment_date,salary_base,pension_base\n"
            b"Jan/2026,ACME,2026-01-31,1000000,100000\n"
        ),
    )

    assert result.imported_periods == 1
    assert result.imported_items == 2
    assert repository.rows[0].employer == "ACME"
    assert repository.rows[0].concept_code == "SALARY_BASE"
    assert repository.rows[1].amount_clp == Decimal("100000")


@pytest.mark.asyncio
async def test_import_payroll_rejects_empty_import() -> None:
    repository = StubPayrollRepository()
    use_case = ImportPayroll(repository)

    with pytest.raises(ValueError, match="did not yield any importable rows"):
        await use_case.from_bytes("sample.csv", b"period,employer,payment_date\n")
