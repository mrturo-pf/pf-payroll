"""Tests for test import payroll."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    ImportPayrollResultDTO,
    ImportPayrollRowDTO,
    ImportedPayrollPeriodDTO,
)
from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.domain.contributions import EmploymentContractKind


class StubPayrollRepository:
    """Test double for Payroll Repository."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.rows: list[object] = []

    async def import_rows(self, rows: list[object]) -> ImportPayrollResultDTO:
        """Import rows."""
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
                    net_pay_difference_clp=getattr(
                        rows[0], "net_pay_difference_clp", None
                    ),
                    net_pay_warning=(
                        None
                        if getattr(rows[0], "net_pay_difference_clp", None)
                        in (None, Decimal("0"))
                        else (
                            "Declared net_pay does not match the imported "
                            "concept totals. Difference: "
                            f"{rows[0].net_pay_difference_clp} CLP."
                        )
                    ),
                )
            ],
        )


class StubPayrollImporter:
    """Test double for Payroll Importer."""

    def __init__(self, rows: list[ImportPayrollRowDTO]) -> None:
        """Initialize the instance."""
        self.rows = rows
        self.calls: list[tuple[str, bytes]] = []

    def read_rows(self, filename: str, content: bytes) -> list[ImportPayrollRowDTO]:
        """Read rows."""
        self.calls.append((filename, content))
        return self.rows


def sample_rows() -> list[ImportPayrollRowDTO]:
    """Sample rows."""
    return [
        ImportPayrollRowDTO(
            employer="ACME",
            period_year=2026,
            period_month=1,
            payment_date=date(2026, 1, 31),
            status="actual",
            employment_contract_kind=EmploymentContractKind.INDEFINITE,
            concept_code="SALARY_BASE",
            amount_clp=Decimal("1000000"),
            declared_net_pay_clp=Decimal("950000"),
            expected_net_pay_clp=Decimal("900000"),
            net_pay_difference_clp=Decimal("50000"),
        ),
        ImportPayrollRowDTO(
            employer="ACME",
            period_year=2026,
            period_month=1,
            payment_date=date(2026, 1, 31),
            status="actual",
            employment_contract_kind=EmploymentContractKind.INDEFINITE,
            concept_code="PENSION_BASE",
            amount_clp=Decimal("100000"),
            declared_net_pay_clp=Decimal("950000"),
            expected_net_pay_clp=Decimal("900000"),
            net_pay_difference_clp=Decimal("50000"),
        ),
    ]


@pytest.mark.asyncio
async def test_import_payroll_reads_csv_and_builds_rows() -> None:
    """Test import payroll reads csv and builds rows."""
    repository = StubPayrollRepository()
    importer = StubPayrollImporter(sample_rows())
    use_case = ImportPayroll(repository, importer)

    payload = b"period,employer,payment_date\nJan/2026,ACME,2026-01-31\n"
    result = await use_case.from_bytes("sample.csv", payload)

    assert result.imported_periods == 1
    assert result.imported_items == 2
    assert importer.calls == [("sample.csv", payload)]
    assert repository.rows[0].employer == "ACME"
    assert repository.rows[0].concept_code == "SALARY_BASE"
    assert repository.rows[0].employment_contract_kind.value == "indefinite"
    assert repository.rows[1].amount_clp == Decimal("100000")


@pytest.mark.asyncio
async def test_import_payroll_rejects_empty_import() -> None:
    """Test import payroll rejects empty import."""
    repository = StubPayrollRepository()
    use_case = ImportPayroll(repository, StubPayrollImporter([]))

    with pytest.raises(ValueError, match="did not yield any importable rows"):
        await use_case.from_bytes("sample.csv", b"period,employer,payment_date\n")


@pytest.mark.asyncio
async def test_import_payroll_adds_net_pay_warning_without_rejecting_import() -> None:
    """Test import payroll adds net pay warning without rejecting import."""
    repository = StubPayrollRepository()
    use_case = ImportPayroll(repository, StubPayrollImporter(sample_rows()))

    result = await use_case.from_bytes("sample.csv", b"sample")

    assert result.periods[0].declared_net_pay_clp == Decimal("950000")
    assert result.periods[0].expected_net_pay_clp == Decimal("900000")
    assert result.periods[0].net_pay_difference_clp == Decimal("50000")
    assert result.periods[0].net_pay_warning == (
        "Declared net_pay does not match the imported concept "
        "totals. Difference: 50000 CLP."
    )
