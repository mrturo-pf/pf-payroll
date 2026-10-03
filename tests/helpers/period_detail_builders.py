"""Shared PayrollPeriodDetailDTO builder for tests.

Several test modules each need a synthetic, already-persisted ACME period
to exercise their own concern (export/import round-trip fidelity,
contribution validation, XLSX serialization) -- before this helper existed
they each hand-rolled a near-identical PayrollPeriodDetailDTO literal
(same id/employer/dates/contract-kind boilerplate, differing only in
items/summary/a couple of overrides). This is the one builder every one of
them should call instead.
"""

from __future__ import annotations

from datetime import date

from payroll.application.dto import (
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)


def build_acme_period_detail(
    *,
    items: list[PayrollItemDetailDTO],
    summary: PayrollSummaryDTO | None,
    period_id: int = 1,
    employer_tax_id: str | None = "76.123.456-7",
    health_plan_ids: tuple[int, ...] | None = None,
    health_institution_is_active: bool | None = None,
) -> PayrollPeriodDetailDTO:
    """Build a synthetic ACME period detail with the boilerplate every caller shares."""
    return PayrollPeriodDetailDTO(
        id=period_id,
        employer_id=1,
        employer_name="ACME",
        employer_tax_id=employer_tax_id,
        employer_country_code="CL",
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        worked_days=30,
        pension_plan_id=1,
        health_plan_id=2,
        health_plan_ids=health_plan_ids,
        health_institution_is_active=health_institution_is_active,
        items=items,
        summary=summary,
    )
