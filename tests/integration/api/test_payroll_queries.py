"""Tests for test payroll queries."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from payroll.application.errors import PayrollPeriodNotFoundError
from payroll.application.dto import (
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollPeriodRangeDTO,
    PayrollSummaryDTO,
)
from payroll.interfaces.api.dependencies import get_payroll_queries
from payroll.interfaces.api.main import app
from payroll.interfaces.api.routes.payroll import (
    _compute_increase,
    get_payroll_period,
)
from helpers.reference_data import (
    sample_payroll_period_detail_dto,
    sample_payroll_summary_dto,
)


def _make_period_range(
    period_year: int,
    period_month: int,
    start_date: date,
    end_date: date,
    net_pay_clp: Decimal | None,
    *,
    is_current: bool = False,
    inferred: bool = False,
    salary_base: Decimal | None = None,
    worked_days: int | None = None,
) -> PayrollPeriodRangeDTO:
    return PayrollPeriodRangeDTO(
        period_year=period_year,
        period_month=period_month,
        start_date=start_date,
        end_date=end_date,
        net_pay_clp=net_pay_clp,
        is_current=is_current,
        inferred=inferred,
        salary_base=salary_base,
        worked_days=worked_days,
    )


class FakePayrollQueries:
    """Test double for Payroll Queries."""

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO:
        """Get period detail."""
        assert period_id == 7
        return sample_payroll_period_detail_dto(
            7,
            employer_tax_id="76.123.456-7",
            employer_ended_at=date(2025, 12, 31),
            health_institution_is_active=False,
            items=[
                PayrollItemDetailDTO(
                    concept_code="SALARY_BASE",
                    concept_name="Base Salary",
                    kind="income",
                    is_taxable=True,
                    amount_clp=Decimal("1000000"),
                    notes=None,
                ),
                PayrollItemDetailDTO(
                    concept_code="PENSION_BASE",
                    concept_name="Pension Base",
                    kind="discount",
                    is_taxable=False,
                    amount_clp=Decimal("100000"),
                    notes="computed",
                ),
            ],
        )

    async def list_period_summaries(self) -> list[PayrollSummaryDTO]:
        """List period summaries."""
        return [sample_payroll_summary_dto(7)]

    async def list_period_ranges(self) -> list[PayrollPeriodRangeDTO]:
        """List period ranges."""
        return [
            PayrollPeriodRangeDTO(
                period_year=2025,
                period_month=12,
                start_date=date(2025, 12, 31),
                end_date=date(2026, 1, 30),
                net_pay_clp=None,
                is_current=False,
                inferred=True,
                increase=None,
                # No salary data (inferred) → increase stays null
            ),
            PayrollPeriodRangeDTO(
                period_year=2026,
                period_month=1,
                start_date=date(2026, 1, 31),
                end_date=date(2026, 2, 27),
                net_pay_clp=Decimal("830000"),
                is_current=True,
                inferred=False,
                increase=None,
            ),
            PayrollPeriodRangeDTO(
                period_year=2026,
                period_month=2,
                start_date=date(2026, 2, 28),
                end_date=date(2026, 3, 30),
                net_pay_clp=None,
                is_current=False,
                inferred=True,
                increase=True,
            ),
        ]


def test_payroll_query_endpoints() -> None:
    """Test payroll query endpoints."""
    app.dependency_overrides[get_payroll_queries] = lambda: FakePayrollQueries()
    client = TestClient(app)

    try:
        range_response = client.get("/payroll/period-range")
        summary_response = client.get("/payroll/summary")
        detail_response = client.get("/payroll/7")
    finally:
        app.dependency_overrides.clear()

    assert range_response.status_code == 200
    assert range_response.json() == [
        {
            "period_year": 2025,
            "period_month": 12,
            "start_date": "2025-12-31",
            "end_date": "2026-01-30",
            "net_pay_clp": None,
            "position": "previous",
            "increase": None,
        },
        {
            "period_year": 2026,
            "period_month": 1,
            "start_date": "2026-01-31",
            "end_date": "2026-02-27",
            "net_pay_clp": "830000",
            "position": "current",
            "increase": None,
        },
        {
            "period_year": 2026,
            "period_month": 2,
            "start_date": "2026-02-28",
            "end_date": "2026-03-30",
            "net_pay_clp": None,
            "position": "future",
            "increase": True,
        },
    ]
    assert summary_response.status_code == 200
    assert summary_response.json() == [
        {
            "period_id": 7,
            "employer_id": 1,
            "employer_name": "ACME",
            "period_year": 2026,
            "period_month": 1,
            "payment_date": "2026-01-31",
            "taxable_income_clp": "1000000",
            "gross_income_clp": "1000000",
            "total_discounts_clp": "170000",
            "net_pay_clp": "830000",
        }
    ]
    assert detail_response.status_code == 200
    assert detail_response.json() == {
        "id": 7,
        "employer_id": 1,
        "employer_name": "ACME",
        "employer_tax_id": "76.123.456-7",
        "employer_country_code": "CL",
        "employer_started_at": "2020-01-01",
        "employer_ended_at": "2025-12-31",
        "period_year": 2026,
        "period_month": 1,
        "payment_date": "2026-01-31",
        "worked_days": 30,
        "status": "actual",
        "employment_contract_kind": "indefinite",
        "pension_plan_id": 1,
        "health_plan_id": 2,
        "items": [
            {
                "concept_code": "SALARY_BASE",
                "concept_name": "Base Salary",
                "kind": "income",
                "is_taxable": True,
                "amount_clp": "1000000",
                "notes": None,
            },
            {
                "concept_code": "PENSION_BASE",
                "concept_name": "Pension Base",
                "kind": "discount",
                "is_taxable": False,
                "amount_clp": "100000",
                "notes": "computed",
            },
        ],
        "summary": {
            "period_id": 7,
            "employer_id": 1,
            "employer_name": "ACME",
            "period_year": 2026,
            "period_month": 1,
            "payment_date": "2026-01-31",
            "taxable_income_clp": "1000000",
            "gross_income_clp": "1000000",
            "total_discounts_clp": "170000",
            "net_pay_clp": "830000",
        },
        "health_institution_is_active": False,
    }


def test_payroll_detail_endpoint_surfaces_not_found() -> None:
    """Test payroll detail endpoint surfaces not found."""

    class ErrorPayrollQueries:
        """Represent the error payroll queries."""

        async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO:
            """Get period detail."""
            raise PayrollPeriodNotFoundError("Payroll period 9 was not found.")

        async def list_period_summaries(self) -> list[PayrollSummaryDTO]:
            """List period summaries."""
            return []

    app.dependency_overrides[get_payroll_queries] = lambda: ErrorPayrollQueries()
    client = TestClient(app)

    try:
        response = client.get("/payroll/9")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Payroll period 9 was not found."}


def test_compute_increase_returns_true_when_normalized_salary_rose() -> None:
    """Increase is true when (salary_base/worked_days)*30 grew vs predecessor."""
    current = _make_period_range(
        2026,
        1,
        date(2026, 1, 31),
        date(2026, 2, 27),
        Decimal("830000"),
        salary_base=Decimal("1200000"),
        worked_days=30,
    )
    predecessor = _make_period_range(
        2025,
        12,
        date(2025, 12, 31),
        date(2026, 1, 30),
        Decimal("780000"),
        salary_base=Decimal("1000000"),
        worked_days=30,
    )
    assert _compute_increase(current, predecessor) is True


def test_compute_increase_returns_false_when_normalized_salary_fell() -> None:
    """Increase is false when (salary_base/worked_days)*30 dropped vs predecessor."""
    current = _make_period_range(
        2026,
        1,
        date(2026, 1, 31),
        date(2026, 2, 27),
        Decimal("780000"),
        salary_base=Decimal("1000000"),
        worked_days=30,
    )
    predecessor = _make_period_range(
        2025,
        12,
        date(2025, 12, 31),
        date(2026, 1, 30),
        Decimal("830000"),
        salary_base=Decimal("1200000"),
        worked_days=30,
    )
    assert _compute_increase(current, predecessor) is False


def test_compute_increase_returns_none_when_predecessor_has_no_salary() -> None:
    """Increase is null when predecessor lacks salary_base data."""
    current = _make_period_range(
        2026,
        1,
        date(2026, 1, 31),
        date(2026, 2, 27),
        None,
        salary_base=Decimal("1000000"),
        worked_days=30,
    )
    predecessor = _make_period_range(
        2025,
        12,
        date(2025, 12, 31),
        date(2026, 1, 30),
        None,
        inferred=True,
    )
    assert _compute_increase(current, predecessor) is None
    assert _compute_increase(current, None) is None


def test_compute_increase_accounts_for_worked_days_normalization() -> None:
    """Normalized salary comparison uses worked_days, not raw salary_base."""
    # Period with fewer worked_days but same salary_base should appear higher normalized
    current = _make_period_range(
        2026,
        1,
        date(2026, 1, 31),
        date(2026, 2, 27),
        None,
        salary_base=Decimal("1000000"),
        worked_days=25,  # (1000000/25)*30 = 1200000
    )
    predecessor = _make_period_range(
        2025,
        12,
        date(2025, 12, 31),
        date(2026, 1, 30),
        None,
        salary_base=Decimal("1000000"),
        worked_days=30,  # (1000000/30)*30 = 1000000
    )
    assert _compute_increase(current, predecessor) is True


def test_period_range_endpoint_computes_increase_for_previous_with_salary_data() -> (
    None
):
    """Endpoint sets increase=true/false for previous periods that have salary data."""

    class SalaryFakeQueries:
        """Test double returning previous periods with salary data."""

        async def list_period_ranges(self) -> list[PayrollPeriodRangeDTO]:
            """List period ranges."""
            return [
                # Oldest previous: no predecessor in window → null
                PayrollPeriodRangeDTO(
                    period_year=2025,
                    period_month=11,
                    start_date=date(2025, 11, 28),
                    end_date=date(2025, 12, 30),
                    net_pay_clp=Decimal("780000"),
                    is_current=False,
                    inferred=False,
                    salary_base=Decimal("1000000"),
                    worked_days=30,
                ),
                # Salary rose vs predecessor → true
                PayrollPeriodRangeDTO(
                    period_year=2025,
                    period_month=12,
                    start_date=date(2025, 12, 31),
                    end_date=date(2026, 1, 30),
                    net_pay_clp=Decimal("830000"),
                    is_current=False,
                    inferred=False,
                    salary_base=Decimal("1200000"),
                    worked_days=30,
                ),
                # Current period with salary data — rose vs December predecessor → true
                PayrollPeriodRangeDTO(
                    period_year=2026,
                    period_month=1,
                    start_date=date(2026, 1, 31),
                    end_date=date(2026, 2, 27),
                    net_pay_clp=Decimal("830000"),
                    is_current=True,
                    inferred=False,
                    salary_base=Decimal("1500000"),
                    worked_days=30,
                ),
                PayrollPeriodRangeDTO(
                    period_year=2026,
                    period_month=2,
                    start_date=date(2026, 2, 28),
                    end_date=date(2026, 3, 30),
                    net_pay_clp=None,
                    is_current=False,
                    inferred=True,
                    increase=False,
                ),
            ]

    app.dependency_overrides[get_payroll_queries] = lambda: SalaryFakeQueries()
    client = TestClient(app)

    try:
        response = client.get("/payroll/period-range")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data[0]["position"] == "previous"
    assert data[0]["increase"] is None  # no predecessor in window
    assert data[1]["position"] == "previous"
    assert data[1]["increase"] is True  # 1200000 > 1000000
    assert data[2]["position"] == "current"
    assert data[2]["increase"] is True  # 1500000 > 1200000 (vs December)
    assert data[3]["position"] == "future"
    assert data[3]["increase"] is False


def test_period_range_oldest_previous_uses_lookback_as_predecessor() -> None:
    """Oldest previous period resolves increase when a lookback ghost is present."""

    class LookbackFakeQueries:
        """Test double with a lookback DTO at the start of the range list."""

        async def list_period_ranges(self) -> list[PayrollPeriodRangeDTO]:
            """List period ranges with a lookback ghost."""
            return [
                # Lookback ghost — not emitted, salary context for oldest previous
                PayrollPeriodRangeDTO(
                    period_year=2025,
                    period_month=10,
                    start_date=date(2025, 10, 31),
                    end_date=date(2025, 11, 29),
                    net_pay_clp=Decimal("700000"),
                    is_current=False,
                    inferred=False,
                    salary_base=Decimal("1000000"),
                    worked_days=30,
                    is_lookback=True,
                ),
                # Oldest previous — can now resolve increase vs lookback
                PayrollPeriodRangeDTO(
                    period_year=2025,
                    period_month=11,
                    start_date=date(2025, 11, 28),
                    end_date=date(2025, 12, 30),
                    net_pay_clp=Decimal("780000"),
                    is_current=False,
                    inferred=False,
                    salary_base=Decimal("1200000"),
                    worked_days=30,
                ),
                PayrollPeriodRangeDTO(
                    period_year=2025,
                    period_month=12,
                    start_date=date(2025, 12, 31),
                    end_date=date(2026, 1, 30),
                    net_pay_clp=Decimal("830000"),
                    is_current=True,
                    inferred=False,
                    salary_base=Decimal("1200000"),
                    worked_days=30,
                ),
                PayrollPeriodRangeDTO(
                    period_year=2026,
                    period_month=1,
                    start_date=date(2026, 1, 31),
                    end_date=date(2026, 2, 27),
                    net_pay_clp=None,
                    is_current=False,
                    inferred=True,
                    increase=True,
                ),
            ]

    app.dependency_overrides[get_payroll_queries] = lambda: LookbackFakeQueries()
    client = TestClient(app)

    try:
        response = client.get("/payroll/period-range")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    # Lookback is NOT in the response
    assert len(data) == 3
    assert data[0]["position"] == "previous"
    assert data[0]["period_month"] == 11
    assert data[0]["increase"] is True  # 1200000 > 1000000 (lookback)
    assert data[1]["position"] == "current"
    assert data[1]["increase"] is False  # 1200000 == 1200000 → not greater → False
    assert data[2]["position"] == "future"


@pytest.mark.asyncio
async def test_payroll_detail_handler_maps_value_errors() -> None:
    """Test payroll detail handler maps value errors."""

    class ErrorPayrollQueries:
        """Represent the error payroll queries."""

        async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO:
            """Get period detail."""
            raise PayrollPeriodNotFoundError("Payroll period 9 was not found.")

    with pytest.raises(HTTPException, match="Payroll period 9 was not found."):
        await get_payroll_period(period_id=9, queries=ErrorPayrollQueries())
