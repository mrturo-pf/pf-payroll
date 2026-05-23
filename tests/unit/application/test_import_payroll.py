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
                    employment_contract_kind=rows[0].employment_contract_kind,
                    item_count=len(rows),
                    declared_net_pay_clp=getattr(rows[0], "declared_net_pay_clp", None),
                    expected_net_pay_clp=getattr(rows[0], "expected_net_pay_clp", None),
                    net_pay_difference_clp=getattr(rows[0], "net_pay_difference_clp", None),
                    net_pay_warning=(
                        None
                        if getattr(rows[0], "net_pay_difference_clp", None) in (None, Decimal("0"))
                        else "Declared net_pay does not match the imported concept totals. Difference: "
                        f"{rows[0].net_pay_difference_clp} CLP."
                    ),
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
            b"period,employer,payment_date,employment_contract_kind,salary_base,pension_base\n"
            b"Jan/2026,ACME,2026-01-31,indefinite,1000000,100000\n"
        ),
    )

    assert result.imported_periods == 1
    assert result.imported_items == 2
    assert repository.rows[0].employer == "ACME"
    assert repository.rows[0].concept_code == "SALARY_BASE"
    assert repository.rows[0].employment_contract_kind.value == "indefinite"
    assert repository.rows[1].amount_clp == Decimal("100000")


@pytest.mark.asyncio
async def test_import_payroll_rejects_empty_import() -> None:
    repository = StubPayrollRepository()
    use_case = ImportPayroll(repository)

    with pytest.raises(ValueError, match="did not yield any importable rows"):
        await use_case.from_bytes("sample.csv", b"period,employer,payment_date\n")


@pytest.mark.asyncio
async def test_import_payroll_adds_net_pay_warning_without_rejecting_import() -> None:
    repository = StubPayrollRepository()
    use_case = ImportPayroll(repository)

    result = await use_case.from_bytes(
        "sample.csv",
        (
            b"period,employer,payment_date,employment_contract_kind,salary_base,pension_base,net_pay\n"
            b"Jan/2026,ACME,2026-01-31,indefinite,1000000,100000,950000\n"
        ),
    )

    assert result.periods[0].declared_net_pay_clp == Decimal("950000")
    assert result.periods[0].expected_net_pay_clp == Decimal("900000")
    assert result.periods[0].net_pay_difference_clp == Decimal("50000")
    assert result.periods[0].net_pay_warning == (
        "Declared net_pay does not match the imported concept totals. Difference: 50000 CLP."
    )
