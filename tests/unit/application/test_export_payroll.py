"""Tests for the ExportPayroll use case."""

from datetime import date

import pytest

from helpers.export_fakes import FakePayrollRepository
from payroll.application.dto import ExportPayrollFiltersDTO, PayrollPeriodDetailDTO
from payroll.application.use_cases.export_payroll import ExportPayroll


class FakeExporter:
    """Test double recording the periods it was asked to serialize."""

    def __init__(self, content: bytes = b"csv-bytes") -> None:
        """Initialize the instance."""
        self.content = content
        self.received_periods: list[PayrollPeriodDetailDTO] | None = None

    def export(self, periods: list[PayrollPeriodDetailDTO]) -> bytes:
        """Record periods and return canned bytes."""
        self.received_periods = periods
        return self.content

    def export_template(self) -> bytes:
        """Return canned template bytes -- unused by ExportPayroll itself."""
        return b"template-bytes"


def _build_detail(period_id: int) -> PayrollPeriodDetailDTO:
    return PayrollPeriodDetailDTO(
        id=period_id,
        employer_id=1,
        employer_name="ACME",
        employer_tax_id=None,
        employer_country_code="CL",
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        worked_days=30,
        pension_plan_id=None,
        health_plan_id=None,
        items=[],
        summary=None,
    )


@pytest.mark.asyncio
async def test_export_payroll_forwards_filters_to_repository() -> None:
    """execute() passes the filters DTO through to the repository unchanged."""
    repository = FakePayrollRepository([])
    exporter = FakeExporter()
    use_case = ExportPayroll(repository, exporter)  # type: ignore[arg-type]
    filters = ExportPayrollFiltersDTO(employer="ACME", period_year=2026, period_month=1)

    await use_case.execute(filters)

    assert repository.received_filters is filters


@pytest.mark.asyncio
async def test_export_payroll_passes_repository_result_to_exporter() -> None:
    """execute() hands the repository's periods to the exporter, unmodified."""
    periods = [_build_detail(1), _build_detail(2)]
    repository = FakePayrollRepository(periods)
    exporter = FakeExporter()
    use_case = ExportPayroll(repository, exporter)  # type: ignore[arg-type]

    await use_case.execute(ExportPayrollFiltersDTO())

    assert exporter.received_periods == periods


@pytest.mark.asyncio
async def test_export_payroll_returns_exporter_bytes() -> None:
    """execute() returns exactly what the exporter produced."""
    repository = FakePayrollRepository([])
    exporter = FakeExporter(content=b"the-actual-file-bytes")
    use_case = ExportPayroll(repository, exporter)  # type: ignore[arg-type]

    result = await use_case.execute(ExportPayrollFiltersDTO())

    assert result == b"the-actual-file-bytes"


@pytest.mark.asyncio
async def test_export_payroll_with_no_filters_still_calls_repository() -> None:
    """A no-filter call (export everything) is not special-cased away."""
    repository = FakePayrollRepository([_build_detail(1)])
    exporter = FakeExporter()
    use_case = ExportPayroll(repository, exporter)  # type: ignore[arg-type]

    await use_case.execute(ExportPayrollFiltersDTO())

    assert repository.received_filters == ExportPayrollFiltersDTO()
