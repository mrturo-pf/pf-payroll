"""Operational payroll dashboard entrypoint."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from html import escape

from payroll.config import settings
from payroll.application.dto import HealthPlanDTO, PayrollPeriodDetailDTO, PayrollSummaryDTO, PensionPlanDTO
from payroll.application.use_cases.payroll_queries import PayrollQueries
from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.infrastructure.db.repositories.payroll_repository import SqlAlchemyPayrollRepository
from payroll.infrastructure.db.repositories.reference_data_repository import SqlAlchemyReferenceDataRepository
from payroll.infrastructure.db.session import SessionLocal

_REQUIRED_CONTRIBUTION_CODES = {
    "PENSION_BASE",
    "PENSION_ADDITIONAL",
    "HEALTH_BASE",
    "HEALTH_ADDITIONAL_UF",
    "UNEMPLOYMENT_INSURANCE",
}


@dataclass(frozen=True, slots=True)
class DashboardPeriodRow:
    period_id: int
    employer_name: str
    period_label: str
    payment_date: str
    status: str
    contract_kind: str
    assigned_plans: str
    missing_items: str
    next_action: str
    action_endpoint: str
    report_url: str | None
    net_pay_clp: str
    net_pay_check: str
    net_pay_check_status: str


def _format_clp(amount: Decimal) -> str:
    return f"${amount:,.0f}".replace(",", ".")


def _format_period(period_year: int, period_month: int) -> str:
    return f"{period_month:02d}/{period_year}"


def _missing_required_items(detail: PayrollPeriodDetailDTO) -> list[str]:
    present_codes = {item.concept_code for item in detail.items}
    missing_codes: list[str] = []
    if detail.pension_plan_id is None or detail.health_plan_id is None:
        missing_codes.append("ASSIGN_PLANS")
    if missing_contributions := sorted(_REQUIRED_CONTRIBUTION_CODES - present_codes):
        missing_codes.extend(missing_contributions)
    if "INCOME_TAX" not in present_codes:
        missing_codes.append("INCOME_TAX")
    if detail.status != "reviewed":
        missing_codes.append("REVIEW_PERIOD")
    return missing_codes


def _next_action(detail: PayrollPeriodDetailDTO) -> tuple[str, str]:
    present_codes = {item.concept_code for item in detail.items}
    if detail.status == "reviewed":
        return ("Download payroll PDF", f"GET /payroll/{detail.id}/report.pdf")
    if detail.pension_plan_id is None or detail.health_plan_id is None:
        return ("Assign pension and health plans", f"POST /payroll/{detail.id}/assign-plans")
    if not _REQUIRED_CONTRIBUTION_CODES.issubset(present_codes):
        return ("Compute contributions", f"POST /payroll/{detail.id}/compute-contributions")
    if "INCOME_TAX" not in present_codes:
        return ("Compute income tax", f"POST /payroll/{detail.id}/compute-tax")
    return ("Review payroll period", f"POST /payroll/{detail.id}/review")


def _assigned_plans_label(detail: PayrollPeriodDetailDTO) -> str:
    if detail.pension_plan_id is None or detail.health_plan_id is None:
        return "Missing"
    return f"Pension #{detail.pension_plan_id} / Health #{detail.health_plan_id}"


def _report_url(detail: PayrollPeriodDetailDTO) -> str | None:
    if detail.status != "reviewed":
        return None
    return f"{settings.api_base_url}/payroll/{detail.id}/report.pdf"


def _net_pay_check(summary: PayrollSummaryDTO) -> tuple[str, str]:
    if (
        summary.declared_net_pay_clp is None
        or summary.expected_net_pay_clp is None
        or summary.net_pay_difference_clp is None
    ):
        return ("No declared net pay", "not_available")
    if summary.net_pay_difference_clp == 0:
        return ("Matches declared net pay", "matched")
    difference = _format_clp(abs(summary.net_pay_difference_clp))
    declared = _format_clp(summary.declared_net_pay_clp)
    expected = _format_clp(summary.expected_net_pay_clp)
    return (f"Mismatch by {difference} (declared {declared} vs expected {expected})", "mismatch")


def _render_report_cell(row: DashboardPeriodRow) -> str:
    if row.report_url is None:
        return "<td>Available after review</td>"
    return (
        f'<td><a href="{escape(row.report_url)}" target="_blank" '
        'rel="noopener noreferrer">Download PDF</a></td>'
    )


def _build_period_row(summary: PayrollSummaryDTO, detail: PayrollPeriodDetailDTO) -> DashboardPeriodRow:
    next_action, action_endpoint = _next_action(detail)
    missing_items = _missing_required_items(detail)
    net_pay_check, net_pay_check_status = _net_pay_check(summary)
    return DashboardPeriodRow(
        period_id=summary.period_id,
        employer_name=summary.employer_name,
        period_label=_format_period(summary.period_year, summary.period_month),
        payment_date=summary.payment_date.isoformat(),
        status=detail.status,
        contract_kind=detail.employment_contract_kind.value,
        assigned_plans=_assigned_plans_label(detail),
        missing_items=", ".join(missing_items) if missing_items else "Ready",
        next_action=next_action,
        action_endpoint=action_endpoint,
        report_url=_report_url(detail),
        net_pay_clp=_format_clp(summary.net_pay_clp),
        net_pay_check=net_pay_check,
        net_pay_check_status=net_pay_check_status,
    )


def _render_plan_options(pension_plans: list[PensionPlanDTO], health_plans: list[HealthPlanDTO]) -> str:
    pension_items = "".join(
        (
            "<li>"
            f"#{plan.id} - {escape(plan.institution_name)} "
            f"(extra {escape(str(plan.additional_rate))}, valid from {plan.valid_from.isoformat()})"
            "</li>"
        )
        for plan in pension_plans
    )
    health_items = "".join(
        (
            "<li>"
            f"#{plan.id} - {escape(plan.institution_name)} / {escape(plan.plan_name or 'Base')} "
            f"({escape(str(plan.contracted_uf))} UF, valid from {plan.valid_from.isoformat()})"
            "</li>"
        )
        for plan in health_plans
    )
    return (
        "<section>"
        "<h2>Available plan snapshots</h2>"
        "<div class='plans'>"
        f"<div><h3>Pension plans</h3><ul>{pension_items or '<li>No pension plans found.</li>'}</ul></div>"
        f"<div><h3>Health plans</h3><ul>{health_items or '<li>No health plans found.</li>'}</ul></div>"
        "</div>"
        "</section>"
    )


def render_dashboard_html(
    period_rows: list[DashboardPeriodRow],
    pension_plans: list[PensionPlanDTO],
    health_plans: list[HealthPlanDTO],
) -> str:
    total_periods = len(period_rows)
    reviewed_periods = sum(1 for row in period_rows if row.status == "reviewed")
    pending_periods = total_periods - reviewed_periods
    matched_periods = sum(1 for row in period_rows if row.net_pay_check_status == "matched")
    mismatched_periods = sum(1 for row in period_rows if row.net_pay_check_status == "mismatch")
    total_net_pay = sum((Decimal(row.net_pay_clp.replace("$", "").replace(".", "")) for row in period_rows), Decimal("0"))
    rows_html = "".join(
        (
            "<tr>"
            f"<td>{row.period_id}</td>"
            f"<td>{escape(row.employer_name)}</td>"
            f"<td>{escape(row.period_label)}</td>"
            f"<td>{escape(row.payment_date)}</td>"
            f"<td>{escape(row.status)}</td>"
            f"<td>{escape(row.contract_kind)}</td>"
            f"<td>{escape(row.assigned_plans)}</td>"
            f"<td>{escape(row.missing_items)}</td>"
            f"<td>{escape(row.net_pay_clp)}</td>"
            f"<td>{escape(row.net_pay_check)}</td>"
            f"{_render_report_cell(row)}"
            "</tr>"
        )
        for row in period_rows
    )
    empty_state = (
        "<p>No payroll periods available yet. Start by importing a file with "
        "<code>POST /payroll/import</code>.</p>"
    )
    table_html = (
        empty_state
        if not period_rows
        else (
            "<table>"
            "<thead><tr>"
            "<th>ID</th><th>Employer</th><th>Period</th><th>Payment date</th><th>Status</th>"
            "<th>Contract</th><th>Plans</th><th>Missing</th><th>Net pay</th><th>Check</th><th>PDF</th>"
            "</tr></thead>"
            f"<tbody>{rows_html}</tbody>"
            "</table>"
        )
    )
    total_net_pay_label = escape(_format_clp(total_net_pay))
    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "  <head>\n"
        "    <meta charset=\"utf-8\">\n"
        "    <title>Payroll operations dashboard</title>\n"
        "    <style>\n"
        "      body { font-family: Arial, sans-serif; margin: 2rem; color: #1f2937; }\n"
        "      .metrics { display: grid; grid-template-columns: repeat(5, minmax(160px, 1fr)); gap: 1rem; margin: 1.5rem 0; }\n"
        "      .card { border: 1px solid #d1d5db; border-radius: 8px; padding: 1rem; background: #f9fafb; }\n"
        "      table { width: 100%; border-collapse: collapse; margin-top: 1rem; }\n"
        "      th, td { border: 1px solid #e5e7eb; padding: 0.75rem; text-align: left; vertical-align: top; }\n"
        "      th { background: #f3f4f6; }\n"
        "      code { white-space: nowrap; }\n"
        "      .plans { display: grid; grid-template-columns: repeat(2, minmax(240px, 1fr)); gap: 1.5rem; }\n"
        "    </style>\n"
        "  </head>\n"
        "  <body>\n"
        "    <h1>Payroll operations dashboard</h1>\n"
        "    <p>Business flow: import -> assign plans -> compute contributions -> compute tax -> review -> PDF.</p>\n"
        "    <div class=\"metrics\">\n"
        f"      <div class=\"card\"><strong>Total periods</strong><div>{total_periods}</div></div>\n"
        f"      <div class=\"card\"><strong>Reviewed periods</strong><div>{reviewed_periods}</div></div>\n"
        f"      <div class=\"card\"><strong>Pending periods</strong><div>{pending_periods}</div></div>\n"
        f"      <div class=\"card\"><strong>Matched net pay</strong><div>{matched_periods}</div></div>\n"
        f"      <div class=\"card\"><strong>Mismatched net pay</strong><div>{mismatched_periods}</div></div>\n"
        f"      <div class=\"card\"><strong>Total net pay</strong><div>{total_net_pay_label}</div></div>\n"
        "    </div>\n"
        "    <section>\n"
        "      <h2>Payroll periods</h2>\n"
        f"      {table_html}\n"
        "    </section>\n"
        f"    {_render_plan_options(pension_plans, health_plans)}\n"
        "  </body>\n"
        "</html>\n"
    )


async def build_dashboard_html() -> str:
    async with SessionLocal() as session:
        payroll_queries = PayrollQueries(SqlAlchemyPayrollRepository(session))
        reference_queries = ReferenceDataQueries(SqlAlchemyReferenceDataRepository(session))
        summaries = await payroll_queries.list_period_summaries()
        details = await asyncio.gather(*(payroll_queries.get_period_detail(summary.period_id) for summary in summaries))
        period_rows = [_build_period_row(summary, detail) for summary, detail in zip(summaries, details, strict=True)]
        pension_plans = await reference_queries.list_pension_plans()
        health_plans = await reference_queries.list_health_plans()
        return render_dashboard_html(period_rows, pension_plans, health_plans)


def main() -> None:
    print(asyncio.run(build_dashboard_html()))


if __name__ == "__main__":
    main()
