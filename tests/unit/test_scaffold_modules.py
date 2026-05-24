"""Tests for test scaffold modules."""

from __future__ import annotations

import asyncio
import importlib
import runpy
import sys
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
import typer
from typer.testing import CliRunner

from payroll.application.dto import MoneyDTO
from payroll.application.use_cases.assign_plans import AssignPlans
from payroll.application.use_cases.market_data import MarketDataQueries
from payroll.application.use_cases.compute_contributions import ComputeContributions
from payroll.application.use_cases.deflate_amounts import DeflateAmounts
from payroll.application.use_cases.generate_payroll_report import GeneratePayrollReport
from payroll.application.use_cases.compute_income_tax import ComputeIncomeTax
from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.application.use_cases.payroll_queries import PayrollQueries
from payroll.application.use_cases.review_payroll_period import ReviewPayrollPeriod
from payroll.application.use_cases.refresh_income_tax_brackets import RefreshIncomeTaxBrackets
from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.application.use_cases.refresh_rates import RefreshRates
from payroll.config import Settings
from payroll.domain.contribution_calculator import ContributionCalculator, quantize_clp
from payroll.domain.contributions import (
    ContributionCap,
    HealthInstitution,
    HealthInstitutionKind,
    HealthPlan,
    PensionInstitution,
    PensionPlan,
)
from payroll.domain.deflation import DeflationCalculator
from payroll.domain.tax_calculator import ChileanTaxCalculator
from payroll.domain.taxes import IncomeTaxBracket
from payroll.domain.entities import PayrollPeriod
from payroll.domain.value_objects import Money
from payroll.infrastructure.importers.xlsx_importer import XlsxPayrollImporter, read_payroll_dataframe, to_long_format
from payroll.infrastructure.logging.logger import logger
from payroll.infrastructure.reporting.weasyprint_payroll_report_renderer import WeasyPrintPayrollReportRenderer
from payroll.infrastructure.rate_providers.chained_provider import ChainedFxProvider
from payroll.interfaces.cli.main import app as cli_app
from payroll.interfaces.dashboard.app import main as dashboard_main
from payroll.shared.constants import DEFAULT_CURRENCY


def test_settings_defaults_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test settings defaults and env override."""
    defaults = Settings()
    assert defaults.env == "development"
    assert defaults.log_level == "INFO"

    monkeypatch.setenv("PAYROLL_ENV", "test")
    monkeypatch.setenv("PAYROLL_DATABASE_URL", "postgresql+asyncpg://example/db")
    overridden = Settings()

    assert overridden.env == "test"
    assert overridden.database_url == "postgresql+asyncpg://example/db"


def test_domain_dataclasses_and_constants() -> None:
    """Test domain dataclasses and constants."""
    payroll_period = PayrollPeriod(
        employer_id=7,
        period_year=2026,
        period_month=5,
        payment_date=date(2026, 5, 31),
    )
    cap = ContributionCap("pension_health", date(2026, 1, 1), None, Decimal("90.5000"))
    pension_plan = PensionPlan(
        id=1,
        institution=PensionInstitution("AFP_UNO", "AFP Uno", Decimal("0.10")),
        valid_from=date(2026, 1, 1),
        valid_to=None,
        additional_rate=Decimal("0.0127"),
    )
    health_plan = HealthPlan(
        id=2,
        institution=HealthInstitution("FONASA", "Fonasa", HealthInstitutionKind.FONASA, Decimal("0.07")),
        valid_from=date(2026, 1, 1),
        valid_to=None,
        plan_name="Base",
        contracted_uf=Decimal("0"),
    )
    tax_bracket = IncomeTaxBracket(
        valid_from=date(2026, 1, 1),
        valid_to=None,
        lower_bound_utm=Decimal("0"),
        upper_bound_utm=Decimal("13.5"),
        marginal_rate=Decimal("0"),
        rebate_utm=Decimal("0"),
    )
    money = Money(amount=Decimal("123.45"))
    dto = MoneyDTO(amount=Decimal("99.99"))

    assert payroll_period.worked_days == 30
    assert cap.value_uf == Decimal("90.5000")
    assert HealthInstitutionKind.FONASA == "fonasa"
    assert pension_plan.additional_rate == Decimal("0.0127")
    assert health_plan.institution.kind is HealthInstitutionKind.FONASA
    assert tax_bracket.upper_bound_utm == Decimal("13.5")
    assert money.currency == "CLP"
    assert dto.currency == DEFAULT_CURRENCY


def test_contribution_calculator_quantizes_and_honors_lower_taxable_amount() -> None:
    """Test contribution calculator quantizes and honors lower taxable amount."""
    calculator = ContributionCalculator()
    cap = ContributionCap("pension_health", date(2026, 1, 1), None, Decimal("90.0600"))

    assert quantize_clp(Decimal("10.6")) == Decimal("11")
    assert calculator.pension_base(Decimal("1000"), cap, Decimal("10000")) == Decimal("1000")
    assert ChileanTaxCalculator() is not None
    assert DeflationCalculator().deflate_amount(Decimal("100"), Decimal("100"), Decimal("110")) == Decimal("110")


def test_use_case_placeholders_are_instantiable() -> None:
    """Test use case placeholders are instantiable."""
    class StubRepository:
        """Test double for Repository."""

        async def import_rows(self, rows: list[object]) -> object:
            """Import rows."""
            return rows

        async def assign_plans(self, command: object) -> object:
            """Assign plans."""
            return command

        async def review_period(self, command: object) -> object:
            """Review period."""
            return command

        async def get_contribution_context(self, command: object) -> object:
            """Get contribution context."""
            return command

        async def save_computed_contributions(self, result: object) -> object:
            """Save computed contributions."""
            return result

        async def list_exchange_rates(self, currency_code: str | None = None) -> list[object]:
            """List exchange rates."""
            return []

        async def list_economic_indices(self, code: str | None = None) -> list[object]:
            """List economic indices."""
            return []

        async def get_exchange_rate_value(self, currency_code: str, rate_date: date) -> Decimal | None:
            """Get exchange rate value."""
            return Decimal("1")

        async def get_economic_index_value(self, code: str, period_year: int, period_month: int) -> Decimal | None:
            """Get economic index value."""
            return Decimal("100")

        async def refresh_rates(self, command: object) -> object:
            """Refresh rates."""
            return command

        async def upsert_income_tax_brackets(self, brackets: list[object]) -> int:
            """Handle upsert income tax brackets."""
            return len(brackets)

        async def get_period_detail(self, period_id: int) -> object:
            """Get period detail."""
            return period_id

        async def list_period_summaries(self) -> list[object]:
            """List period summaries."""
            return []

        async def get_income_tax_context(self, command: object) -> object:
            """Get income tax context."""
            return command

        async def get_income_tax_bracket(self, payment_date: date, taxable_base_utm: Decimal) -> object:
            """Get income tax bracket."""
            return object()

        async def save_computed_income_tax(self, result: object) -> object:
            """Save computed income tax."""
            return result

    assert isinstance(ImportPayroll(StubRepository(), XlsxPayrollImporter()), ImportPayroll)
    assert isinstance(PayrollQueries(StubRepository()), PayrollQueries)
    assert isinstance(AssignPlans(StubRepository()), AssignPlans)
    assert isinstance(GeneratePayrollReport(StubRepository(), WeasyPrintPayrollReportRenderer()), GeneratePayrollReport)
    assert isinstance(ReviewPayrollPeriod(StubRepository()), ReviewPayrollPeriod)
    assert isinstance(ReferenceDataQueries(object()), ReferenceDataQueries)
    assert isinstance(MarketDataQueries(StubRepository()), MarketDataQueries)
    assert isinstance(ComputeContributions(StubRepository(), StubRepository()), ComputeContributions)
    assert isinstance(DeflateAmounts(StubRepository(), StubRepository()), DeflateAmounts)
    assert isinstance(ComputeIncomeTax(StubRepository(), StubRepository()), ComputeIncomeTax)
    assert isinstance(RefreshRates(StubRepository()), RefreshRates)
    assert isinstance(RefreshIncomeTaxBrackets(StubRepository(), object()), RefreshIncomeTaxBrackets)


def test_dashboard_main_prints_rendered_html(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test dashboard main prints rendered html."""
    async def fake_build_dashboard_html() -> str:
        """Handle fake build dashboard html."""
        return "<html>dashboard</html>"

    monkeypatch.setattr("payroll.interfaces.dashboard.app.build_dashboard_html", fake_build_dashboard_html)

    dashboard_main()

    assert capsys.readouterr().out.strip() == "<html>dashboard</html>"


def test_xlsx_importer_transforms_and_reads_files() -> None:
    """Test xlsx importer transforms and reads files."""
    source = pd.DataFrame(
        [
            {
                "period": "Jan/2026",
                "employer": "ACME",
                "payment_date": "2026-01-31",
                "employment_contract_kind": "indefinite",
                "salary_base": 1000,
            }
        ]
    )

    result = to_long_format(source)
    dataframe = read_payroll_dataframe(
        "sample.csv",
        __import__("io").BytesIO(
            b"period,employer,payment_date,employment_contract_kind,salary_base\n"
            b"Jan/2026,ACME,2026-01-31,indefinite,1000\n"
        ),
    )

    assert result.to_dict(orient="records")[0]["concept_code"] == "SALARY_BASE"
    assert result.to_dict(orient="records")[0]["employment_contract_kind"].value == "indefinite"
    assert dataframe.iloc[0]["employer"] == "ACME"


@pytest.mark.asyncio
async def test_chained_provider_returns_first_available_rate() -> None:
    """Test chained provider returns first available rate."""
    class Provider:
        """Provide provider."""

        def __init__(self, value: Decimal | None) -> None:
            """Initialize the instance."""
            self.value = value

        async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
            """Handle fetch rate."""
            assert currency_code == "USD"
            assert on == date(2026, 5, 1)
            return self.value

    provider = ChainedFxProvider([Provider(None), Provider(Decimal("950.12")), Provider(Decimal("999.99"))])

    result = await provider.fetch_rate("USD", date(2026, 5, 1))

    assert result == Decimal("950.12")


@pytest.mark.asyncio
async def test_chained_provider_returns_none_when_all_providers_miss() -> None:
    """Test chained provider returns none when all providers miss."""
    class Provider:
        """Provide provider."""

        async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
            """Handle fetch rate."""
            assert currency_code == "EUR"
            assert on == date(2026, 5, 2)
            return None

    provider = ChainedFxProvider([Provider(), Provider()])

    result = await provider.fetch_rate("EUR", date(2026, 5, 2))

    assert result is None


def test_logger_is_available() -> None:
    """Test logger is available."""
    assert logger is not None


def test_cli_health_command() -> None:
    """Test cli health command."""
    result = CliRunner().invoke(cli_app, ["health"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"


def test_importing_db_modules_exposes_expected_types() -> None:
    """Test importing db modules exposes expected types."""
    base_module = importlib.import_module("payroll.infrastructure.db.base")
    session_module = importlib.import_module("payroll.infrastructure.db.session")

    assert base_module.Base.__name__ == "Base"
    assert session_module.engine is not None
    assert session_module.SessionLocal is not None


def test_package_modules_import() -> None:
    """Test package modules import."""
    modules = [
        "payroll",
        "payroll.application",
        "payroll.application.ports",
        "payroll.application.ports.rate_provider",
        "payroll.application.ports.repositories",
        "payroll.application.use_cases",
        "payroll.domain",
        "payroll.infrastructure",
        "payroll.infrastructure.db",
        "payroll.infrastructure.importers",
        "payroll.infrastructure.logging",
        "payroll.infrastructure.rate_providers",
        "payroll.interfaces",
        "payroll.interfaces.api",
        "payroll.interfaces.api.routes",
        "payroll.interfaces.cli",
        "payroll.interfaces.dashboard",
        "payroll.shared",
    ]

    for module_name in modules:
        assert importlib.import_module(module_name) is not None


def test_cli_module_runs_as_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test cli module runs as main."""
    called: list[bool] = []

    def fake_call(self: typer.Typer, *args: object, **kwargs: object) -> None:
        """Handle fake call."""
        called.append(True)

    monkeypatch.setattr(typer.Typer, "__call__", fake_call)
    sys.modules.pop("payroll.interfaces.cli.main", None)

    runpy.run_module("payroll.interfaces.cli.main", run_name="__main__")

    assert called == [True]


def test_dashboard_module_runs_as_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test dashboard module runs as main."""
    def fake_run(coro: object) -> str:
        """Handle fake run."""
        getattr(coro, "close")()
        return "<html>dashboard</html>"

    monkeypatch.setattr(asyncio, "run", fake_run)
    sys.modules.pop("payroll.interfaces.dashboard.app", None)

    runpy.run_module("payroll.interfaces.dashboard.app", run_name="__main__")

    assert capsys.readouterr().out.strip() == "<html>dashboard</html>"
