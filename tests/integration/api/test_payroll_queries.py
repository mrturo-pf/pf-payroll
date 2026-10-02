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
    _compute_net_pay_clp_today,
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
    net_pay_uf: Decimal | None = None,
    fixed_uf_clp: Decimal = Decimal("0"),
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
        net_pay_uf=net_pay_uf,
        fixed_uf_clp=fixed_uf_clp,
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

    async def list_period_ranges(
        self,
        *,
        today: date | None = None,
        previous_months: int | None = None,
        future_months: int | None = None,
    ) -> list[PayrollPeriodRangeDTO]:
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
                increase=Decimal("0.00"),
            ),
        ]


def test_payroll_query_endpoints() -> None:
    """Test payroll query endpoints."""
    app.dependency_overrides[get_payroll_queries] = lambda: FakePayrollQueries()
    client = TestClient(app, headers={"X-API-Key": "test-key"})

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
            "net_pay_clp_today": None,
            "net_pay_usd": None,
            "net_pay_eur": None,
            "net_pay_uf": None,
        },
        {
            "period_year": 2026,
            "period_month": 1,
            "start_date": "2026-01-31",
            "end_date": "2026-02-27",
            "net_pay_clp": 830000,
            "position": "current",
            "increase": None,
            "net_pay_clp_today": None,
            "net_pay_usd": None,
            "net_pay_eur": None,
            "net_pay_uf": None,
        },
        {
            "period_year": 2026,
            "period_month": 2,
            "start_date": "2026-02-28",
            "end_date": "2026-03-30",
            "net_pay_clp": None,
            "position": "future",
            "increase": 0.0,
            "net_pay_clp_today": None,
            "net_pay_usd": None,
            "net_pay_eur": None,
            "net_pay_uf": None,
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


def test_payroll_period_range_forwards_month_query_params() -> None:
    """previous_months/future_months query params reach the use case unchanged."""

    class CapturingFakeQueries:
        """Records the previous_months/future_months it was called with."""

        def __init__(self) -> None:
            self.calls: list[tuple[int | None, int | None]] = []

        async def list_period_ranges(
            self,
            *,
            today: date | None = None,
            previous_months: int | None = None,
            future_months: int | None = None,
        ) -> list[PayrollPeriodRangeDTO]:
            """List period ranges, recording the requested counts."""
            self.calls.append((previous_months, future_months))
            return []

    fake_queries = CapturingFakeQueries()
    app.dependency_overrides[get_payroll_queries] = lambda: fake_queries
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        no_params_response = client.get("/payroll/period-range")
        explicit_response = client.get(
            "/payroll/period-range",
            params={"previous_months": 6, "future_months": 3},
        )
    finally:
        app.dependency_overrides.clear()

    assert no_params_response.status_code == 200
    assert explicit_response.status_code == 200
    assert fake_queries.calls == [(None, None), (6, 3)]


def test_payroll_period_range_rejects_future_months_over_12() -> None:
    """future_months > 12 is a 422 validation error, never silently clamped."""
    app.dependency_overrides[get_payroll_queries] = lambda: FakePayrollQueries()
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.get("/payroll/period-range", params={"future_months": 13})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_payroll_period_range_rejects_negative_previous_months() -> None:
    """A negative previous_months is a 422 validation error."""
    app.dependency_overrides[get_payroll_queries] = lambda: FakePayrollQueries()
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.get("/payroll/period-range", params={"previous_months": -1})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


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
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.get("/payroll/9")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Payroll period 9 was not found."}


def test_compute_increase_returns_positive_pct_when_normalized_salary_rose() -> None:
    """Increase is the percentage change when (salary_base/worked_days)*30 grew."""
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
    assert _compute_increase(current, predecessor) == Decimal("20.00")


def test_compute_increase_returns_negative_pct_when_normalized_salary_fell() -> None:
    """Increase is the percentage change (negative) when normalized salary dropped."""
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
    assert _compute_increase(current, predecessor) == Decimal("-16.67")


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
    assert _compute_increase(current, predecessor) == Decimal("20.00")


def test_compute_increase_returns_none_for_zero_salary_predecessor() -> None:
    """A zero-salary predecessor baseline makes percent change undefined."""
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
        salary_base=Decimal("0"),
        worked_days=30,
    )
    assert _compute_increase(current, predecessor) is None


def test_compute_net_pay_clp_today_scales_salary_and_repricess_uf_discount() -> None:
    """net_pay_clp_today splits scalable_clp (salary-driven) from fixed_uf_clp.

    Synthetic but clean numbers, hand-verified: previous period paid
    1,000,000 net (of which 50,000 is a UF-denominated health top-up, so
    scalable_clp = 1,050,000) when its own UF/CLP rate was 50,000
    (1,000,000/20). The employee's *real* recorded salary_base grew 10%
    since then (1,650,000 vs. 1,500,000 normalized) -- that 1.1 ratio is
    what scales the salary-driven 1,050,000 to 1,155,000, *not* any UF
    appreciation. Today's UF/CLP rate is 40,000 (3,000,000/75): the
    50,000-CLP health top-up was exactly 1 UF back then (50,000/50,000),
    so it reprices to 1 x 40,000 = 40,000 today. Net: 1,155,000 - 40,000 =
    1,115,000 -- not a figure either a whole-net-pay UF-ratio scaling or a
    whole-net-pay salary-ratio scaling alone would produce.
    """
    current = _make_period_range(
        2026,
        9,
        date(2026, 9, 29),
        date(2026, 10, 28),
        Decimal("3000000"),
        is_current=True,
        net_pay_uf=Decimal("75"),
        salary_base=Decimal("1650000"),
        worked_days=30,
    )
    previous = _make_period_range(
        2025,
        9,
        date(2025, 9, 27),
        date(2025, 10, 28),
        Decimal("1000000"),
        net_pay_uf=Decimal("20"),
        salary_base=Decimal("1500000"),
        worked_days=30,
        fixed_uf_clp=Decimal("50000"),
    )
    result = _compute_net_pay_clp_today(previous, current)
    assert result == Decimal("1115000")


def test_compute_net_pay_clp_today_defaults_fixed_uf_clp_to_zero() -> None:
    """No HEALTH_ADDITIONAL_UF item for the period -> fixed_uf_clp defaults to 0.

    With no UF-driven discount at all, the whole net_pay is "scalable" and
    is scaled purely by the real salary_base ratio (here, flat: 1.0) --
    result equals the historical net_pay_clp unchanged.
    """
    current = _make_period_range(
        2026,
        9,
        date(2026, 9, 29),
        date(2026, 10, 28),
        Decimal("3000000"),
        is_current=True,
        net_pay_uf=Decimal("75"),
        salary_base=Decimal("1500000"),
        worked_days=30,
    )
    previous = _make_period_range(
        2025,
        9,
        date(2025, 9, 27),
        date(2025, 10, 28),
        Decimal("1000000"),
        net_pay_uf=Decimal("20"),
        salary_base=Decimal("1500000"),
        worked_days=30,
    )
    assert _compute_net_pay_clp_today(previous, current) == Decimal("1000000")


def test_compute_net_pay_clp_today_returns_none_without_current() -> None:
    """No current period resolved (e.g. none found at all) -> None."""
    previous = _make_period_range(
        2025,
        9,
        date(2025, 9, 27),
        date(2025, 10, 28),
        Decimal("1000000"),
        net_pay_uf=Decimal("20"),
        salary_base=Decimal("1500000"),
        worked_days=30,
    )
    assert _compute_net_pay_clp_today(previous, None) is None


def test_compute_net_pay_clp_today_returns_none_without_salary_or_uf_data() -> None:
    """Missing net_pay_uf, salary_base or worked_days on either side -> None."""
    current = _make_period_range(
        2026,
        9,
        date(2026, 9, 29),
        date(2026, 10, 28),
        Decimal("3000000"),
        is_current=True,
        net_pay_uf=Decimal("75"),
        salary_base=Decimal("1500000"),
        worked_days=30,
    )
    base_previous_kwargs = {
        "net_pay_clp": Decimal("1000000"),
        "net_pay_uf": Decimal("20"),
        "salary_base": Decimal("1500000"),
        "worked_days": 30,
    }
    for missing_field in ("net_pay_uf", "salary_base", "worked_days"):
        kwargs = {**base_previous_kwargs, missing_field: None}
        previous = _make_period_range(
            2025,
            9,
            date(2025, 9, 27),
            date(2025, 10, 28),
            kwargs.pop("net_pay_clp"),
            **kwargs,
        )
        assert _compute_net_pay_clp_today(previous, current) is None


def test_compute_net_pay_clp_today_returns_none_without_current_rate_ingredients() -> (
    None
):
    """current.net_pay_clp/net_pay_uf/salary_base missing or zero -> None."""
    previous = _make_period_range(
        2025,
        9,
        date(2025, 9, 27),
        date(2025, 10, 28),
        Decimal("1000000"),
        net_pay_uf=Decimal("20"),
        salary_base=Decimal("1500000"),
        worked_days=30,
    )
    base_current_kwargs = {
        "net_pay_clp": Decimal("3000000"),
        "is_current": True,
        "net_pay_uf": Decimal("75"),
        "salary_base": Decimal("1500000"),
        "worked_days": 30,
    }
    for override in (
        {"net_pay_clp": None},
        {"net_pay_uf": None},
        {"net_pay_uf": Decimal("0")},
        {"salary_base": None},
        {"worked_days": None},
    ):
        kwargs = {**base_current_kwargs, **override}
        current = _make_period_range(
            2026,
            9,
            date(2026, 9, 29),
            date(2026, 10, 28),
            kwargs.pop("net_pay_clp"),
            **kwargs,
        )
        assert _compute_net_pay_clp_today(previous, current) is None


def test_compute_net_pay_clp_today_returns_none_for_zero_normalized_salary() -> None:
    """A zero historical normalized salary_base makes the ratio undefined."""
    current = _make_period_range(
        2026,
        9,
        date(2026, 9, 29),
        date(2026, 10, 28),
        Decimal("3000000"),
        is_current=True,
        net_pay_uf=Decimal("75"),
        salary_base=Decimal("1500000"),
        worked_days=30,
    )
    previous = _make_period_range(
        2025,
        9,
        date(2025, 9, 27),
        date(2025, 10, 28),
        Decimal("1000000"),
        net_pay_uf=Decimal("20"),
        salary_base=Decimal("0"),
        worked_days=30,
    )
    assert _compute_net_pay_clp_today(previous, current) is None


def test_period_range_endpoint_computes_increase_for_previous_with_salary_data() -> (
    None
):
    """Endpoint sets increase=true/false for previous periods that have salary data."""

    class SalaryFakeQueries:
        """Test double returning previous periods with salary data."""

        async def list_period_ranges(
            self,
            *,
            today: date | None = None,
            previous_months: int | None = None,
            future_months: int | None = None,
        ) -> list[PayrollPeriodRangeDTO]:
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
                    increase=Decimal("0.00"),
                ),
            ]

    app.dependency_overrides[get_payroll_queries] = lambda: SalaryFakeQueries()
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.get("/payroll/period-range")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data[0]["position"] == "previous"
    assert data[0]["increase"] is None  # no predecessor in window
    assert data[1]["position"] == "previous"
    assert data[1]["increase"] == 20.0  # (1200000-1000000)/1000000 * 100
    assert data[2]["position"] == "current"
    assert data[2]["increase"] == 25.0  # (1500000-1200000)/1200000 * 100
    assert data[3]["position"] == "future"
    assert data[3]["increase"] == 0.0


def test_period_range_oldest_previous_uses_lookback_as_predecessor() -> None:
    """Oldest previous period resolves increase when a lookback ghost is present."""

    class LookbackFakeQueries:
        """Test double with a lookback DTO at the start of the range list."""

        async def list_period_ranges(
            self,
            *,
            today: date | None = None,
            previous_months: int | None = None,
            future_months: int | None = None,
        ) -> list[PayrollPeriodRangeDTO]:
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
                    increase=Decimal("0.00"),
                ),
            ]

    app.dependency_overrides[get_payroll_queries] = lambda: LookbackFakeQueries()
    client = TestClient(app, headers={"X-API-Key": "test-key"})

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
    assert data[0]["increase"] == 20.0  # (1200000-1000000)/1000000 * 100 (lookback)
    assert data[1]["position"] == "current"
    assert data[1]["increase"] == 0.0  # 1200000 == 1200000 -> 0% change
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
