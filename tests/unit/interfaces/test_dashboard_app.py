"""Tests for test dashboard app."""

import asyncio
from datetime import date
from decimal import Decimal

import payroll.interfaces.dashboard.app as dashboard_app
from payroll.application.dto import (
    HealthPlanDTO,
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
    PensionPlanDTO,
)
from payroll.domain.contributions import EmploymentContractKind, HealthInstitutionKind
from payroll.interfaces.dashboard.app import (
    _assigned_plans_label,
    _build_period_row,
    _format_clp,
    _format_period,
    _missing_required_items,
    _net_pay_check,
    _next_action,
    _report_url,
    render_dashboard_html,
)


def sample_summary() -> PayrollSummaryDTO:
    """Sample summary."""
    return PayrollSummaryDTO(
        period_id=7,
        employer_id=1,
        employer_name="ACME",
        period_year=2026,
        period_month=4,
        payment_date=date(2026, 4, 30),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1250000"),
        total_discounts_clp=Decimal("180000"),
        net_pay_clp=Decimal("1070000"),
        declared_net_pay_clp=Decimal("1070000"),
        expected_net_pay_clp=Decimal("1070000"),
        net_pay_difference_clp=Decimal("0"),
    )


def sample_detail(
    *,
    status: str = "projected",
    pension_plan_id: int | None = None,
    health_plan_id: int | None = None,
    item_codes: list[str] | None = None,
    health_institution_is_active: bool | None = None,
) -> PayrollPeriodDetailDTO:
    """Sample detail."""
    codes = item_codes or ["SALARY_BASE"]
    items = [
        PayrollItemDetailDTO(
            concept_code=code,
            concept_name=code.title(),
            kind="discount" if code != "SALARY_BASE" else "income",
            is_taxable=code == "SALARY_BASE",
            amount_clp=Decimal("1000"),
            notes=None,
        )
        for code in codes
    ]
    return PayrollPeriodDetailDTO(
        id=7,
        employer_id=1,
        employer_name="ACME",
        employer_tax_id="76000000-1",
        employer_country_code="CL",
        employer_started_at=date(2020, 1, 1),
        employer_ended_at=None,
        period_year=2026,
        period_month=4,
        payment_date=date(2026, 4, 30),
        worked_days=30,
        status=status,
        employment_contract_kind=EmploymentContractKind.INDEFINITE,
        pension_plan_id=pension_plan_id,
        health_plan_id=health_plan_id,
        items=items,
        summary=sample_summary(),
        health_institution_is_active=health_institution_is_active,
    )


def sample_pension_plan() -> PensionPlanDTO:
    """Sample pension plan."""
    return PensionPlanDTO(
        id=1,
        institution_code="AFP_UNO",
        institution_name="AFP Uno",
        valid_from=date(2026, 1, 1),
        valid_to=None,
        additional_rate=Decimal("0.0127"),
    )


def sample_health_plan() -> HealthPlanDTO:
    """Sample health plan."""
    return HealthPlanDTO(
        id=2,
        institution_code="FONASA",
        institution_name="Fonasa",
        institution_kind=HealthInstitutionKind.FONASA,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        plan_name="Base",
        contracted_uf=Decimal("0"),
    )


def test_dashboard_format_helpers() -> None:
    """Test dashboard format helpers."""
    assert _format_clp(Decimal("1234567")) == "$1.234.567"
    assert _format_period(2026, 4) == "04/2026"
    assert _net_pay_check(sample_summary()) == ("Matches declared net pay", "matched")
    pending_summary = PayrollSummaryDTO(
        period_id=7,
        employer_id=1,
        employer_name="ACME",
        period_year=2026,
        period_month=4,
        payment_date=date(2026, 4, 30),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1250000"),
        total_discounts_clp=Decimal("180000"),
        net_pay_clp=Decimal("1070000"),
        declared_net_pay_clp=Decimal("1070000"),
    )
    assert _net_pay_check(pending_summary) == (
        "Pending computed reconciliation",
        "pending",
    )


def test_missing_required_items_marks_every_pending_step() -> None:
    """Test missing required items marks every pending step."""
    missing = _missing_required_items(sample_detail())

    assert missing == [
        "ASSIGN_PLANS",
        "HEALTH_ADDITIONAL_UF",
        "HEALTH_BASE",
        "PENSION_ADDITIONAL",
        "PENSION_BASE",
        "UNEMPLOYMENT_INSURANCE",
        "INCOME_TAX",
        "REVIEW_PERIOD",
    ]


def test_next_action_prioritizes_assign_plans() -> None:
    """Test next action prioritizes assign plans."""
    action, endpoint = _next_action(sample_detail())

    assert action == "Assign pension and health plans"
    assert endpoint == "POST /payroll/7/assign-plans"


def test_next_action_prioritizes_contributions_then_tax_then_review_then_pdf() -> None:
    """Test next action prioritizes contributions then tax then review then pdf."""
    action, endpoint = _next_action(
        sample_detail(
            pension_plan_id=1,
            health_plan_id=2,
        )
    )
    assert action == "Compute contributions"
    assert endpoint == "POST /payroll/7/compute-contributions"

    action, endpoint = _next_action(
        sample_detail(
            pension_plan_id=1,
            health_plan_id=2,
            item_codes=[
                "SALARY_BASE",
                "PENSION_BASE",
                "PENSION_ADDITIONAL",
                "HEALTH_BASE",
                "HEALTH_ADDITIONAL_UF",
                "UNEMPLOYMENT_INSURANCE",
            ],
        )
    )
    assert action == "Compute income tax"
    assert endpoint == "POST /payroll/7/compute-tax"

    action, endpoint = _next_action(
        sample_detail(
            pension_plan_id=1,
            health_plan_id=2,
            item_codes=[
                "SALARY_BASE",
                "PENSION_BASE",
                "PENSION_ADDITIONAL",
                "HEALTH_BASE",
                "HEALTH_ADDITIONAL_UF",
                "UNEMPLOYMENT_INSURANCE",
                "INCOME_TAX",
            ],
        )
    )
    assert action == "Review payroll period"
    assert endpoint == "POST /payroll/7/review"

    action, endpoint = _next_action(
        sample_detail(
            status="reviewed",
            pension_plan_id=1,
            health_plan_id=2,
            item_codes=[
                "SALARY_BASE",
                "PENSION_BASE",
                "PENSION_ADDITIONAL",
                "HEALTH_BASE",
                "HEALTH_ADDITIONAL_UF",
                "UNEMPLOYMENT_INSURANCE",
                "INCOME_TAX",
            ],
        )
    )
    assert action == "Download payroll PDF"
    assert endpoint == "GET /payroll/7/report.pdf"


def test_assigned_plans_label_and_period_row() -> None:
    """Test assigned plans label and period row."""
    detail = sample_detail(
        status="reviewed",
        pension_plan_id=1,
        health_plan_id=2,
        item_codes=[
            "SALARY_BASE",
            "PENSION_BASE",
            "PENSION_ADDITIONAL",
            "HEALTH_BASE",
            "HEALTH_ADDITIONAL_UF",
            "UNEMPLOYMENT_INSURANCE",
            "INCOME_TAX",
        ],
    )
    row = _build_period_row(sample_summary(), detail)

    assert _assigned_plans_label(sample_detail()) == "Missing"
    assert _report_url(sample_detail()) is None
    assert _report_url(detail) == "http://127.0.0.1:8000/payroll/7/report.pdf"
    assert row.assigned_plans == "Pension #1 / Health #2"
    assert row.missing_items == "Ready"
    assert row.report_url == "http://127.0.0.1:8000/payroll/7/report.pdf"
    assert row.net_pay_clp == "$1.070.000"
    assert row.net_pay_check == "Matches declared net pay"


def test_assigned_plans_label_marks_inactive_health_institution() -> None:
    """Test assigned plans label surfaces inactive health institutions."""
    detail = sample_detail(
        pension_plan_id=1,
        health_plan_id=2,
        health_institution_is_active=False,
    )

    assert (
        _assigned_plans_label(detail)
        == "Pension #1 / Health #2 (Inactive health institution)"
    )


def test_render_dashboard_html_handles_empty_and_populated_states() -> None:
    """Test render dashboard html handles empty and populated states."""
    empty_html = render_dashboard_html([], [], [])
    assert "No payroll periods available yet" in empty_html
    assert "No pension plans found." in empty_html
    assert "No health plans found." in empty_html

    detail = sample_detail(
        status="reviewed",
        pension_plan_id=1,
        health_plan_id=2,
        item_codes=[
            "SALARY_BASE",
            "PENSION_BASE",
            "PENSION_ADDITIONAL",
            "HEALTH_BASE",
            "HEALTH_ADDITIONAL_UF",
            "UNEMPLOYMENT_INSURANCE",
            "INCOME_TAX",
        ],
    )
    row = _build_period_row(sample_summary(), detail)
    html = render_dashboard_html([row], [sample_pension_plan()], [sample_health_plan()])

    assert "Payroll operations dashboard" in html
    assert "Business flow: import -&gt; assign plans" not in html
    assert (
        "Business flow: import -> assign plans -> compute contributions "
        "-> compute tax -> review -> PDF." in html
    )
    assert "<th>Next action</th>" not in html
    assert "<th>Check</th>" in html
    assert "GET /payroll/7/report.pdf" not in html
    assert 'href="http://127.0.0.1:8000/payroll/7/report.pdf"' in html
    assert "Download PDF</a>" in html
    assert "Matched net pay" in html
    assert "Matches declared net pay" in html
    assert "AFP Uno" in html
    assert "Fonasa" in html

    pending_html = render_dashboard_html(
        [_build_period_row(sample_summary(), sample_detail())],
        [sample_pension_plan()],
        [sample_health_plan()],
    )
    assert "Available after review" in pending_html


def test_dashboard_renders_mismatch_and_missing_net_pay_states() -> None:
    """Test dashboard renders mismatch and missing net pay states."""
    mismatch_summary = PayrollSummaryDTO(
        period_id=8,
        employer_id=1,
        employer_name="ACME",
        period_year=2026,
        period_month=5,
        payment_date=date(2026, 5, 31),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1250000"),
        total_discounts_clp=Decimal("180000"),
        net_pay_clp=Decimal("1070000"),
        declared_net_pay_clp=Decimal("1050000"),
        expected_net_pay_clp=Decimal("1070000"),
        net_pay_difference_clp=Decimal("-20000"),
    )
    mismatch_row = _build_period_row(mismatch_summary, sample_detail())
    assert (
        mismatch_row.net_pay_check
        == "Mismatch by $20.000 (declared $1.050.000 vs expected $1.070.000)"
    )

    pending_summary = PayrollSummaryDTO(
        period_id=10,
        employer_id=1,
        employer_name="ACME",
        period_year=2026,
        period_month=7,
        payment_date=date(2026, 7, 31),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1250000"),
        total_discounts_clp=Decimal("180000"),
        net_pay_clp=Decimal("1070000"),
        declared_net_pay_clp=Decimal("1050000"),
    )
    pending_row = _build_period_row(pending_summary, sample_detail())
    assert pending_row.net_pay_check == "Pending computed reconciliation"

    missing_summary = PayrollSummaryDTO(
        period_id=9,
        employer_id=1,
        employer_name="ACME",
        period_year=2026,
        period_month=6,
        payment_date=date(2026, 6, 30),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1250000"),
        total_discounts_clp=Decimal("180000"),
        net_pay_clp=Decimal("1070000"),
    )
    missing_row = _build_period_row(missing_summary, sample_detail())
    assert missing_row.net_pay_check == "No declared net pay"


def test_build_dashboard_html_uses_queries_and_renders_result(monkeypatch) -> None:
    """Test build dashboard html uses queries and renders result."""

    class FakeSessionContext:
        """Test double for Session Context."""

        async def __aenter__(self) -> object:
            """Enter the async context manager."""
            return object()

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            """Exit the async context manager."""
            return None

    reviewed_detail = sample_detail(
        status="reviewed",
        pension_plan_id=1,
        health_plan_id=2,
        item_codes=[
            "SALARY_BASE",
            "PENSION_BASE",
            "PENSION_ADDITIONAL",
            "HEALTH_BASE",
            "HEALTH_ADDITIONAL_UF",
            "UNEMPLOYMENT_INSURANCE",
            "INCOME_TAX",
        ],
    )

    class FakePayrollQueries:
        """Test double for Payroll Queries."""

        def __init__(self, repository: object) -> None:
            """Initialize the instance."""
            assert repository == "payroll-repo"

        async def list_period_summaries(self) -> list[PayrollSummaryDTO]:
            """List period summaries."""
            return [sample_summary()]

        async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO:
            """Get period detail."""
            assert period_id == 7
            return reviewed_detail

    class FakeReferenceDataQueries:
        """Test double for Reference Data Queries."""

        def __init__(self, repository: object) -> None:
            """Initialize the instance."""
            assert repository == "reference-repo"

        async def list_pension_plans(self) -> list[PensionPlanDTO]:
            """List pension plans."""
            return [sample_pension_plan()]

        async def list_health_plans(self) -> list[HealthPlanDTO]:
            """List health plans."""
            return [sample_health_plan()]

    monkeypatch.setattr(dashboard_app, "SessionLocal", lambda: FakeSessionContext())
    monkeypatch.setattr(
        dashboard_app, "SqlAlchemyPayrollRepository", lambda session: "payroll-repo"
    )
    monkeypatch.setattr(
        dashboard_app,
        "SqlAlchemyReferenceDataRepository",
        lambda session: "reference-repo",
    )
    monkeypatch.setattr(dashboard_app, "PayrollQueries", FakePayrollQueries)
    monkeypatch.setattr(dashboard_app, "ReferenceDataQueries", FakeReferenceDataQueries)

    html = asyncio.run(dashboard_app.build_dashboard_html())

    assert "Payroll operations dashboard" in html
    assert "Download PDF" in html
    assert "AFP Uno" in html
