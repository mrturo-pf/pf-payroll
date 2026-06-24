"""Tests for test cli main."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import payroll.interfaces.cli.main as cli_main
from payroll.application.dto import (
    GeneratedPayrollReportDTO,
    ImportPayrollResultDTO,
    ImportedPayrollPeriodDTO,
    MarketDataSyncRequestDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
    SyncRecentMarketDataResultDTO,
)
from payroll.domain.contributions import EmploymentContractKind
from helpers.interface_stubs import sample_health_plan, sample_pension_plan
from helpers.reference_data import (
    sample_acme_april_2026_period_detail_dto,
    sample_acme_april_2026_summary_dto,
)


@dataclass(frozen=True)
class SamplePayload:
    """Represent Sample Payload."""

    amount: Decimal
    when: date


def sample_summary() -> PayrollSummaryDTO:
    """Sample summary."""
    return sample_acme_april_2026_summary_dto()


def sample_detail() -> PayrollPeriodDetailDTO:
    """Sample detail."""
    return sample_acme_april_2026_period_detail_dto(
        status="reviewed", pension_plan_id=1, health_plan_id=2
    )


class _FakeSessionContext:
    """Test double for session context manager, shared across CLI tests."""

    async def __aenter__(self) -> object:
        """Enter the async context manager."""
        return "session"

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Exit the async context manager."""
        return None


class _FakeDualRepoUseCase:
    """Test double for use cases that accept payroll + market-data repositories."""

    def __init__(
        self, payroll_repository: object, market_data_repository: object
    ) -> None:
        """Initialize the instance."""
        assert payroll_repository == "payroll-repo"
        assert market_data_repository == "market-repo"

    async def execute(self, command: object) -> object:
        """Handle execute."""
        return command


class _FakeImportPayrollBase:
    """Base for Import Payroll doubles: validates constructor args and CSV input."""

    def __init__(self, repository: object, importer: object) -> None:
        """Initialize the instance."""
        assert repository == "payroll-repo"
        assert importer == "importer"

    @staticmethod
    def _assert_sample_csv(filename: str, content: bytes) -> None:
        """Assert the filename and content match the shared sample CSV fixture."""
        assert filename == "sample.csv"
        assert content == b"period_month,period_year,employer\n"


class _FakeProcessImportedPayrollPeriods:
    """Test double for imported-payroll post-processing, shared across CLI tests."""

    def __init__(
        self,
        repository: object,
        market_data_repository: object,
        complementary_insurance_repository: object,
    ) -> None:
        """Initialize the instance."""

    async def execute(self, result: ImportPayrollResultDTO) -> ImportPayrollResultDTO:
        """Handle execute."""
        return result


def test_json_default_serializes_supported_values() -> None:
    """Test json default serializes supported values."""
    assert cli_main._json_default(
        SamplePayload(amount=Decimal("1.5"), when=date(2026, 4, 30))
    ) == {
        "amount": Decimal("1.5"),
        "when": date(2026, 4, 30),
    }
    assert cli_main._json_default(Decimal("1.5")) == "1.5"
    assert cli_main._json_default(date(2026, 4, 30)) == "2026-04-30"
    assert cli_main._json_default(EmploymentContractKind.INDEFINITE) == "indefinite"


def test_json_default_rejects_unknown_values() -> None:
    """Test json default rejects unknown values."""
    with pytest.raises(TypeError):
        cli_main._json_default(object())


def test_emit_json_prints_sorted_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Test emit json prints sorted json."""
    cli_main._emit_json({"b": 2, "a": Decimal("1")})

    assert capsys.readouterr().out == '{\n  "a": "1",\n  "b": 2\n}\n'


def test_run_command_returns_result() -> None:
    """Test run command returns result."""

    async def coro() -> str:
        """Handle coro."""
        return "ok"

    assert cli_main._run_command(coro()) == "ok"


def test_run_command_converts_value_error_into_exit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test run command converts value error into exit."""

    async def coro() -> str:
        """Handle coro."""
        raise ValueError("boom")

    with pytest.raises(typer.Exit):
        cli_main._run_command(coro())

    assert capsys.readouterr().err == "boom\n"


def test_run_command_converts_os_error_into_exit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test run command converts os error into exit."""

    async def coro() -> str:
        """Handle coro."""
        raise OSError("disk error")

    with pytest.raises(typer.Exit):
        cli_main._run_command(coro())

    assert capsys.readouterr().err == "disk error\n"


def test_parse_optional_decimal_supports_valid_none_and_invalid_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test parse optional decimal supports valid none and invalid values."""
    assert cli_main._parse_optional_decimal("uf_value_clp", None) is None
    assert cli_main._parse_optional_decimal("uf_value_clp", "39000.5") == Decimal(
        "39000.5"
    )

    with pytest.raises(typer.Exit):
        cli_main._parse_optional_decimal("uf_value_clp", "invalid")

    assert capsys.readouterr().err == "uf_value_clp must be a valid decimal value.\n"


def test_cli_async_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test cli async helpers."""
    sample_file = tmp_path / "sample.csv"
    sample_file.write_text("period_month,period_year,employer\n")

    class FakeImportPayroll(_FakeImportPayrollBase):
        """Test double for Import Payroll."""

        async def from_bytes(self, filename: str, content: bytes) -> object:
            """Create from bytes."""
            self._assert_sample_csv(filename, content)
            return {"imported": True}

    class FakePayrollQueries:
        """Test double for Payroll Queries."""

        def __init__(self, repository: object) -> None:
            """Initialize the instance."""
            assert repository == "payroll-repo"

        async def list_period_summaries(self) -> object:
            """List period summaries."""
            return [sample_summary()]

        async def get_period_detail(self, period_id: int) -> object:
            """Get period detail."""
            assert period_id == 7
            return sample_detail()

    class FakeReferenceDataQueries:
        """Test double for Reference Data Queries."""

        def __init__(self, repository: object) -> None:
            """Initialize the instance."""
            assert repository == "reference-repo"

        async def list_pension_plans(self) -> object:
            """List pension plans."""
            return [sample_pension_plan()]

        async def list_health_plans(self) -> object:
            """List health plans."""
            return [sample_health_plan()]

    class FakeAssignPlans:
        """Test double for Assign Plans."""

        def __init__(self, repository: object) -> None:
            """Initialize the instance."""
            assert repository == "payroll-repo"

        async def execute(self, command: object) -> object:
            """Handle execute."""
            return command

    class FakeReviewPayrollPeriod:
        """Test double for Review Payroll Period."""

        def __init__(self, repository: object) -> None:
            """Initialize the instance."""
            assert repository == "payroll-repo"

        async def execute(self, command: object) -> object:
            """Handle execute."""
            return command

    class FakeGeneratePayrollReport:
        """Test double for Generate Payroll Report."""

        def __init__(self, repository: object, renderer: object) -> None:
            """Initialize the instance."""
            assert repository == "payroll-repo"
            assert renderer == "renderer"

        async def execute(self, period_id: int) -> GeneratedPayrollReportDTO:
            """Handle execute."""
            assert period_id == 7
            return GeneratedPayrollReportDTO(
                period_id=7, filename="payroll-period-7.pdf", content=b"%PDF"
            )

    monkeypatch.setattr(cli_main, "SessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(
        cli_main, "SqlAlchemyPayrollRepository", lambda session: "payroll-repo"
    )
    monkeypatch.setattr(
        cli_main, "SqlAlchemyReferenceDataRepository", lambda session: "reference-repo"
    )
    monkeypatch.setattr(
        cli_main, "SqlAlchemyMarketDataRepository", lambda session: "market-repo"
    )
    monkeypatch.setattr(cli_main, "WeasyPrintPayrollReportRenderer", lambda: "renderer")
    monkeypatch.setattr(cli_main, "XlsxPayrollImporter", lambda: "importer")
    monkeypatch.setattr(cli_main, "ImportPayroll", FakeImportPayroll)
    monkeypatch.setattr(cli_main, "PayrollQueries", FakePayrollQueries)
    monkeypatch.setattr(cli_main, "ReferenceDataQueries", FakeReferenceDataQueries)
    monkeypatch.setattr(cli_main, "AssignPlans", FakeAssignPlans)
    monkeypatch.setattr(cli_main, "ComputeContributions", _FakeDualRepoUseCase)
    monkeypatch.setattr(cli_main, "ComputeIncomeTax", _FakeDualRepoUseCase)
    monkeypatch.setattr(cli_main, "ReviewPayrollPeriod", FakeReviewPayrollPeriod)
    monkeypatch.setattr(cli_main, "GeneratePayrollReport", FakeGeneratePayrollReport)

    assert asyncio.run(cli_main._import_payroll_async(sample_file)) == {
        "imported": True
    }
    assert asyncio.run(cli_main._list_period_summaries_async())[0].period_id == 7
    assert asyncio.run(cli_main._get_period_detail_async(7)).id == 7
    assert (
        asyncio.run(cli_main._list_plan_snapshots_async())["pension_plans"][0].id == 1
    )
    assert asyncio.run(cli_main._assign_plans_async(7, 11, 22)).pension_plan_id == 11
    assert asyncio.run(
        cli_main._compute_contributions_async(7, 11, 22, Decimal("39000"))
    ).uf_value_clp == Decimal("39000")
    assert asyncio.run(
        cli_main._compute_income_tax_async(7, Decimal("68000"))
    ).utm_value_clp == Decimal("68000")
    assert asyncio.run(cli_main._review_period_async(7)).period_id == 7
    assert (
        asyncio.run(cli_main._generate_payroll_report_async(7)).filename
        == "payroll-period-7.pdf"
    )


def test_import_payroll_async_syncs_requested_market_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test import payroll eagerly syncs the requested market data."""
    sample_file = tmp_path / "sample.csv"
    sample_file.write_text("period_month,period_year,employer\n")

    class FakeImportPayroll(_FakeImportPayrollBase):
        """Test double for Import Payroll."""

        async def from_bytes(
            self, filename: str, content: bytes
        ) -> ImportPayrollResultDTO:
            """Create from bytes."""
            self._assert_sample_csv(filename, content)
            return ImportPayrollResultDTO(
                imported_periods=1,
                imported_items=1,
                periods=[
                    ImportedPayrollPeriodDTO(
                        id=1,
                        employer="ACME",
                        period_year=2026,
                        period_month=4,
                        payment_date=date(2026, 4, 29),
                        worked_days=30,
                        status="actual",
                        employment_contract_kind=EmploymentContractKind.INDEFINITE,
                        item_count=1,
                    )
                ],
                market_data_sync_request=MarketDataSyncRequestDTO(
                    exchange_rate_dates={"UF": [date(2026, 4, 29)]}
                ),
            )

    class FakeMarketDataSyncUseCase:
        """Test double for market-data sync."""

        async def execute_request_and_collect_remaining(
            self, request: MarketDataSyncRequestDTO
        ) -> tuple[SyncRecentMarketDataResultDTO, MarketDataSyncRequestDTO | None]:
            """Handle execute request and collect remaining."""
            assert request == MarketDataSyncRequestDTO(
                exchange_rate_dates={"UF": [date(2026, 4, 29)]}
            )
            return (
                SyncRecentMarketDataResultDTO(
                    requested_exchange_rates=1,
                    requested_economic_indices=0,
                    upserted_exchange_rates=1,
                    upserted_economic_indices=0,
                ),
                None,
            )

    monkeypatch.setattr(cli_main, "SessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(
        cli_main, "SqlAlchemyPayrollRepository", lambda session: "payroll-repo"
    )
    monkeypatch.setattr(cli_main, "XlsxPayrollImporter", lambda: "importer")
    monkeypatch.setattr(cli_main, "ImportPayroll", FakeImportPayroll)
    monkeypatch.setattr(
        cli_main,
        "build_market_data_sync_use_case",
        lambda session: FakeMarketDataSyncUseCase(),
    )
    monkeypatch.setattr(
        cli_main,
        "ProcessImportedPayrollPeriods",
        _FakeProcessImportedPayrollPeriods,
    )

    result = asyncio.run(cli_main._import_payroll_async(sample_file))

    assert result == {
        "imported_periods": 1,
        "imported_items": 1,
        "periods": [
            ImportedPayrollPeriodDTO(
                id=1,
                employer="ACME",
                period_year=2026,
                period_month=4,
                payment_date=date(2026, 4, 29),
                worked_days=30,
                status="actual",
                employment_contract_kind=EmploymentContractKind.INDEFINITE,
                item_count=1,
            )
        ],
        "market_data_sync_result": SyncRecentMarketDataResultDTO(
            requested_exchange_rates=1,
            requested_economic_indices=0,
            upserted_exchange_rates=1,
            upserted_economic_indices=0,
        ),
        "market_data_sync_request": None,
    }


def test_import_payroll_async_processes_periods_without_market_sync(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test import payroll post-processes DTO results without market sync work."""
    sample_file = tmp_path / "sample.csv"
    sample_file.write_text("period_month,period_year,employer\n")

    import_result = ImportPayrollResultDTO(
        imported_periods=1,
        imported_items=1,
        periods=[
            ImportedPayrollPeriodDTO(
                id=1,
                employer="ACME",
                period_year=2026,
                period_month=4,
                payment_date=date(2026, 4, 29),
                status="actual",
                employment_contract_kind=EmploymentContractKind.INDEFINITE,
                item_count=1,
            )
        ],
    )

    class FakeImportPayroll:
        """Test double for Import Payroll."""

        def __init__(self, repository: object, importer: object) -> None:
            """Initialize the instance."""

        async def from_bytes(
            self, filename: str, content: bytes
        ) -> ImportPayrollResultDTO:
            """Create from bytes."""
            return import_result

    monkeypatch.setattr(cli_main, "SessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(
        cli_main, "SqlAlchemyPayrollRepository", lambda session: "payroll-repo"
    )
    monkeypatch.setattr(
        cli_main, "SqlAlchemyMarketDataRepository", lambda session: "market-repo"
    )
    monkeypatch.setattr(cli_main, "XlsxPayrollImporter", lambda: "importer")
    monkeypatch.setattr(cli_main, "ImportPayroll", FakeImportPayroll)
    monkeypatch.setattr(
        cli_main,
        "ProcessImportedPayrollPeriods",
        _FakeProcessImportedPayrollPeriods,
    )

    result = asyncio.run(cli_main._import_payroll_async(sample_file))

    assert result == import_result


def test_cli_business_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test cli business commands."""
    source_file = tmp_path / "sample.csv"
    source_file.write_text("period_month,period_year,employer\n")
    report_path = tmp_path / "report.pdf"

    async def fake_import_payroll_async(file_path: Path) -> object:
        """Handle fake import payroll async."""
        assert file_path == source_file
        return {"kind": "import", "file": file_path.name}

    async def fake_list_period_summaries_async() -> object:
        """Handle fake list period summaries async."""
        return [sample_summary()]

    async def fake_get_period_detail_async(period_id: int) -> object:
        """Handle fake get period detail async."""
        assert period_id == 7
        return sample_detail()

    async def fake_list_plan_snapshots_async() -> object:
        """Handle fake list plan snapshots async."""
        return {
            "pension_plans": [sample_pension_plan()],
            "health_plans": [sample_health_plan()],
        }

    async def fake_assign_plans_async(
        period_id: int, pension_plan_id: int, health_plan_id: int
    ) -> object:
        """Handle fake assign plans async."""
        return {
            "period_id": period_id,
            "pension_plan_id": pension_plan_id,
            "health_plan_id": health_plan_id,
        }

    async def fake_compute_contributions_async(
        period_id: int,
        pension_plan_id: int,
        health_plan_id: int,
        uf_value_clp: Decimal | None,
    ) -> object:
        """Handle fake compute contributions async."""
        return {
            "period_id": period_id,
            "pension_plan_id": pension_plan_id,
            "health_plan_id": health_plan_id,
            "uf_value_clp": uf_value_clp,
        }

    async def fake_compute_income_tax_async(
        period_id: int, utm_value_clp: Decimal | None
    ) -> object:
        """Handle fake compute income tax async."""
        return {"period_id": period_id, "utm_value_clp": utm_value_clp}

    async def fake_review_period_async(period_id: int) -> object:
        """Handle fake review period async."""
        return {"period_id": period_id, "status": "reviewed"}

    async def fake_generate_payroll_report_async(
        period_id: int,
    ) -> GeneratedPayrollReportDTO:
        """Handle fake generate payroll report async."""
        assert period_id == 7
        return GeneratedPayrollReportDTO(
            period_id=period_id,
            filename="payroll-period-7.pdf",
            content=b"%PDF-test",
        )

    monkeypatch.setattr(cli_main, "_import_payroll_async", fake_import_payroll_async)
    monkeypatch.setattr(
        cli_main, "_list_period_summaries_async", fake_list_period_summaries_async
    )
    monkeypatch.setattr(
        cli_main, "_get_period_detail_async", fake_get_period_detail_async
    )
    monkeypatch.setattr(
        cli_main, "_list_plan_snapshots_async", fake_list_plan_snapshots_async
    )
    monkeypatch.setattr(cli_main, "_assign_plans_async", fake_assign_plans_async)
    monkeypatch.setattr(
        cli_main, "_compute_contributions_async", fake_compute_contributions_async
    )
    monkeypatch.setattr(
        cli_main, "_compute_income_tax_async", fake_compute_income_tax_async
    )
    monkeypatch.setattr(cli_main, "_review_period_async", fake_review_period_async)
    monkeypatch.setattr(
        cli_main, "_generate_payroll_report_async", fake_generate_payroll_report_async
    )

    runner = CliRunner()

    result = runner.invoke(cli_main.app, ["health"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"

    result = runner.invoke(cli_main.app, ["import-payroll", str(source_file)])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"file": "sample.csv", "kind": "import"}

    result = runner.invoke(cli_main.app, ["summary"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)[0]["period_id"] == 7

    result = runner.invoke(cli_main.app, ["period-detail", "7"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "reviewed"

    result = runner.invoke(cli_main.app, ["plan-snapshots"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["pension_plans"][0]["id"] == 1
    assert payload["health_plans"][0]["id"] == 2

    result = runner.invoke(cli_main.app, ["assign-plans", "7", "11", "22"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["pension_plan_id"] == 11

    result = runner.invoke(
        cli_main.app,
        ["compute-contributions", "7", "11", "22", "--uf-value-clp", "39000"],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["uf_value_clp"] == "39000"

    result = runner.invoke(
        cli_main.app, ["compute-tax", "7", "--utm-value-clp", "68000"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["utm_value_clp"] == "68000"

    result = runner.invoke(cli_main.app, ["review", "7"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "reviewed"

    result = runner.invoke(
        cli_main.app, ["report-pdf", "7", "--output", str(report_path)]
    )
    assert result.exit_code == 0
    assert report_path.read_bytes() == b"%PDF-test"
    assert json.loads(result.stdout) == {
        "bytes_written": 9,
        "filename": "payroll-period-7.pdf",
        "output_path": str(report_path),
        "period_id": 7,
    }


def test_report_pdf_uses_default_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test report pdf uses default output path."""

    async def fake_generate_payroll_report_async(
        period_id: int,
    ) -> GeneratedPayrollReportDTO:
        """Handle fake generate payroll report async."""
        assert period_id == 9
        return GeneratedPayrollReportDTO(
            period_id=9, filename="payroll-period-9.pdf", content=b"%PDF"
        )

    monkeypatch.setattr(
        cli_main, "_generate_payroll_report_async", fake_generate_payroll_report_async
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli_main.app, ["report-pdf", "9"])

    assert result.exit_code == 0
    assert (tmp_path / "payroll-period-9.pdf").read_bytes() == b"%PDF"
    assert json.loads(result.stdout)["output_path"] == "payroll-period-9.pdf"
