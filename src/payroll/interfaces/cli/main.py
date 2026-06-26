"""Typer CLI entrypoint."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from collections.abc import Coroutine
from typing import Annotated, Any, cast

import typer

from payroll.application.dto import (
    AssignPlansCommandDTO,
    ComputeContributionsCommandDTO,
    ComputeIncomeTaxCommandDTO,
    GeneratedPayrollReportDTO,
    ReviewPayrollPeriodCommandDTO,
)
from payroll.application.use_cases.assign_plans import AssignPlans
from payroll.application.use_cases.compute_contributions import ComputeContributions
from payroll.application.use_cases.compute_income_tax import ComputeIncomeTax
from payroll.application.use_cases.generate_payroll_report import GeneratePayrollReport
from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.application.use_cases.payroll_queries import PayrollQueries
from payroll.application.use_cases.process_imported_payroll_periods import (
    ProcessImportedPayrollPeriods,
)
from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.application.use_cases.review_payroll_period import ReviewPayrollPeriod
from payroll.config import settings
from payroll.infrastructure.http.financial_data_client import FinancialDataClient
from payroll.infrastructure.http.income_tax_bracket_client import IncomeTaxBracketClient
from payroll.infrastructure.importers.xlsx_importer import XlsxPayrollImporter
from payroll.infrastructure.reporting.weasyprint_payroll_report_renderer import (
    WeasyPrintPayrollReportRenderer,
)
from payroll.infrastructure.db.repositories.complementary_insurance_repository import (
    SqlAlchemyComplementaryInsuranceRepository,
)
from payroll.infrastructure.db.repositories.payroll_repository import (
    SqlAlchemyPayrollRepository,
)
from payroll.infrastructure.db.repositories.reference_data_repository import (
    SqlAlchemyReferenceDataRepository,
)
from payroll.interfaces.session import SessionLocal, open_session

app = typer.Typer(help="Payroll CLI")


def _open_session():
    """Open a session using the local factory."""
    return open_session(SessionLocal)


def _build_financial_data_client() -> FinancialDataClient:
    """Build the shared FinancialDataClient from settings."""
    return FinancialDataClient(
        base_url=settings.financial_data_base_url,
        api_key=settings.financial_data_api_key,
        cache_ttl_seconds=settings.financial_data_cache_ttl_seconds,
    )


def _build_income_tax_bracket_client() -> IncomeTaxBracketClient:
    """Build the shared IncomeTaxBracketClient from settings."""
    return IncomeTaxBracketClient(
        base_url=settings.financial_data_base_url,
        api_key=settings.financial_data_api_key,
        cache_ttl_seconds=settings.financial_data_cache_ttl_seconds,
    )


def _json_default(value: object) -> Any:
    """Handle json default."""
    if not isinstance(value, type) and is_dataclass(value):
        return asdict(cast(Any, value))
    if isinstance(value, (date, Decimal)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable.")


def _emit_json(payload: object) -> None:
    """Handle emit json."""
    typer.echo(json.dumps(payload, default=_json_default, indent=2, sort_keys=True))


def _run_command[T](coro: Coroutine[Any, Any, T]) -> T:
    """Handle run command."""
    try:
        return asyncio.run(coro)
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _parse_optional_decimal(name: str, value: str | None) -> Decimal | None:
    """Handle parse optional decimal."""
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        typer.echo(f"{name} must be a valid decimal value.", err=True)
        raise typer.Exit(code=1) from exc


async def _import_payroll_async(file_path: Path) -> object:
    """Handle import payroll async."""
    async with _open_session() as session:
        result = await ImportPayroll(
            SqlAlchemyPayrollRepository(session), XlsxPayrollImporter()
        ).from_bytes(file_path.name, file_path.read_bytes())
        return await ProcessImportedPayrollPeriods(
            SqlAlchemyPayrollRepository(session),
            _build_financial_data_client(),
            SqlAlchemyComplementaryInsuranceRepository(session),
            _build_income_tax_bracket_client(),
        ).execute(result)


async def _list_period_summaries_async() -> object:
    """Handle list period summaries async."""
    async with _open_session() as session:
        use_case = PayrollQueries(SqlAlchemyPayrollRepository(session))
        return await use_case.list_period_summaries()


async def _get_period_detail_async(period_id: int) -> object:
    """Handle get period detail async."""
    async with _open_session() as session:
        use_case = PayrollQueries(SqlAlchemyPayrollRepository(session))
        return await use_case.get_period_detail(period_id)


async def _list_plan_snapshots_async() -> dict[str, object]:
    """Handle list plan snapshots async."""
    async with _open_session() as session:
        use_case = ReferenceDataQueries(SqlAlchemyReferenceDataRepository(session))
        return {
            "pension_plans": await use_case.list_pension_plans(),
            "health_plans": await use_case.list_health_plans(),
        }


async def _assign_plans_async(
    period_id: int, pension_plan_id: int, health_plan_id: int
) -> object:
    """Handle assign plans async."""
    async with _open_session() as session:
        use_case = AssignPlans(SqlAlchemyPayrollRepository(session))
        return await use_case.execute(
            AssignPlansCommandDTO(
                period_id=period_id,
                pension_plan_id=pension_plan_id,
                health_plan_id=health_plan_id,
            )
        )


async def _compute_contributions_async(
    period_id: int,
    pension_plan_id: int,
    health_plan_id: int,
    uf_value_clp: Decimal | None,
) -> object:
    """Handle compute contributions async."""
    async with _open_session() as session:
        payroll_repository = SqlAlchemyPayrollRepository(session)
        use_case = ComputeContributions(
            payroll_repository, _build_financial_data_client()
        )
        return await use_case.execute(
            ComputeContributionsCommandDTO(
                period_id=period_id,
                pension_plan_id=pension_plan_id,
                health_plan_id=health_plan_id,
                uf_value_clp=uf_value_clp,
            )
        )


async def _compute_income_tax_async(
    period_id: int, utm_value_clp: Decimal | None
) -> object:
    """Handle compute income tax async."""
    async with _open_session() as session:
        payroll_repository = SqlAlchemyPayrollRepository(session)
        use_case = ComputeIncomeTax(
            payroll_repository,
            _build_financial_data_client(),
            _build_income_tax_bracket_client(),
        )
        return await use_case.execute(
            ComputeIncomeTaxCommandDTO(
                period_id=period_id,
                utm_value_clp=utm_value_clp,
            )
        )


async def _review_period_async(period_id: int) -> object:
    """Handle review period async."""
    async with _open_session() as session:
        use_case = ReviewPayrollPeriod(SqlAlchemyPayrollRepository(session))
        return await use_case.execute(
            ReviewPayrollPeriodCommandDTO(period_id=period_id)
        )


async def _generate_payroll_report_async(period_id: int) -> GeneratedPayrollReportDTO:
    """Handle generate payroll report async."""
    async with _open_session() as session:
        use_case = GeneratePayrollReport(
            SqlAlchemyPayrollRepository(session),
            WeasyPrintPayrollReportRenderer(),
        )
        return await use_case.execute(period_id)


@app.callback()
def main() -> None:
    """Payroll CLI."""


@app.command()
def health() -> None:
    """Handle health."""
    typer.echo("ok")


@app.command("import-payroll")
def import_payroll(
    file_path: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True)
    ],
) -> None:
    """Import payroll."""
    _emit_json(_run_command(_import_payroll_async(file_path)))


@app.command("summary")
def list_summaries() -> None:
    """List summaries."""
    _emit_json(_run_command(_list_period_summaries_async()))


@app.command("period-detail")
def period_detail(period_id: int) -> None:
    """Handle period detail."""
    _emit_json(_run_command(_get_period_detail_async(period_id)))


@app.command("plan-snapshots")
def plan_snapshots() -> None:
    """Handle plan snapshots."""
    _emit_json(_run_command(_list_plan_snapshots_async()))


@app.command("assign-plans")
def assign_plans(period_id: int, pension_plan_id: int, health_plan_id: int) -> None:
    """Assign plans."""
    _emit_json(
        _run_command(_assign_plans_async(period_id, pension_plan_id, health_plan_id))
    )


@app.command("compute-contributions")
def compute_contributions(
    period_id: int,
    pension_plan_id: int,
    health_plan_id: int,
    uf_value_clp: Annotated[str | None, typer.Option("--uf-value-clp")] = None,
) -> None:
    """Compute contributions."""
    _emit_json(
        _run_command(
            _compute_contributions_async(
                period_id,
                pension_plan_id,
                health_plan_id,
                _parse_optional_decimal("uf_value_clp", uf_value_clp),
            )
        )
    )


@app.command("compute-tax")
def compute_tax(
    period_id: int,
    utm_value_clp: Annotated[str | None, typer.Option("--utm-value-clp")] = None,
) -> None:
    """Compute tax."""
    _emit_json(
        _run_command(
            _compute_income_tax_async(
                period_id, _parse_optional_decimal("utm_value_clp", utm_value_clp)
            )
        )
    )


@app.command("review")
def review(period_id: int) -> None:
    """Review."""
    _emit_json(_run_command(_review_period_async(period_id)))


@app.command("report-pdf")
def report_pdf(
    period_id: int,
    output: Annotated[
        Path | None, typer.Option("--output", dir_okay=False, writable=True)
    ] = None,
) -> None:
    """Handle report pdf."""
    report: GeneratedPayrollReportDTO = _run_command(
        _generate_payroll_report_async(period_id)
    )
    output_path = output or Path(report.filename)
    output_path.write_bytes(report.content)
    _emit_json(
        {
            "period_id": report.period_id,
            "filename": report.filename,
            "output_path": str(output_path),
            "bytes_written": len(report.content),
        }
    )


if __name__ == "__main__":
    app()
