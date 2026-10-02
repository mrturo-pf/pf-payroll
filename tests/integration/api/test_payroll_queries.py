"""Tests for test payroll queries."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from payroll.application.errors import PayrollPeriodNotFoundError
from payroll.application.dto import (
    PayrollPeriodRangeContextDTO,
    PayrollPeriodRangeDTO,
)
from helpers.reference_data import (
    PayrollPeriodRangesStubMixin,
    sample_payroll_period_range_context_dto,
)
from payroll.interfaces.api.dependencies import get_payroll_queries
from payroll.interfaces.api.main import app
from payroll.interfaces.api.routes.payroll import (
    _compute_increase,
    _compute_net_pay_clp_today,
    get_payroll_period,
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
    period_id: int | None = None,
    employer_id: int | None = None,
    employer_name: str | None = None,
    gross_income_clp: Decimal | None = None,
    taxable_income_clp: Decimal | None = None,
    total_discounts_clp: Decimal | None = None,
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
        period_id=period_id,
        employer_id=employer_id,
        employer_name=employer_name,
        gross_income_clp=gross_income_clp,
        taxable_income_clp=taxable_income_clp,
        total_discounts_clp=total_discounts_clp,
    )


class FakePayrollQueries(PayrollPeriodRangesStubMixin):
    """Test double for Payroll Queries."""

    async def get_period_range(self, period_id: int) -> PayrollPeriodRangeContextDTO:
        """Get a single period in the unified shape."""
        assert period_id == 7
        target = _make_period_range(
            2026,
            1,
            date(2026, 1, 31),
            date(2026, 2, 27),
            Decimal("830000"),
            is_current=True,
            salary_base=Decimal("1500000"),
            worked_days=30,
            period_id=7,
            employer_id=1,
            employer_name="ACME",
            gross_income_clp=Decimal("1000000"),
            taxable_income_clp=Decimal("1000000"),
            total_discounts_clp=Decimal("170000"),
        )
        predecessor = _make_period_range(
            2025,
            12,
            date(2025, 12, 31),
            date(2026, 1, 30),
            Decimal("780000"),
            salary_base=Decimal("1200000"),
            worked_days=30,
            period_id=6,
            employer_id=1,
            employer_name="ACME",
        )
        return sample_payroll_period_range_context_dto(target, predecessor=predecessor)

    def _build_period_ranges(
        self,
        *,
        today: date | None,
        previous_months: int | None,
        future_months: int | None,
    ) -> list[PayrollPeriodRangeDTO]:
        """Build the fixed period triplet backing list_period_ranges()."""
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
                period_id=7,
                employer_id=1,
                employer_name="ACME",
                gross_income_clp=Decimal("1000000"),
                taxable_income_clp=Decimal("1000000"),
                total_discounts_clp=Decimal("170000"),
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
        list_response = client.get("/payroll")
        detail_response = client.get("/payroll/7")
    finally:
        app.dependency_overrides.clear()

    assert list_response.status_code == 200
    assert list_response.json() == [
        {
            "id": None,
            "employer": None,
            "period": {
                "year": 2025,
                "month": 12,
                "date_range": {"start": "2025-12-31", "end": "2026-01-30"},
                "timeframe": "previous",
            },
            "amount": {
                "gross_income_clp": None,
                "taxable_income_clp": None,
                "total_discounts_clp": None,
                "net_pay_clp": None,
                "net_pay_uf": None,
                "net_pay_usd": None,
                "net_pay_eur": None,
                "increase": None,
                "net_pay_clp_today": None,
            },
        },
        {
            "id": 7,
            "employer": {"id": 1, "name": "ACME"},
            "period": {
                "year": 2026,
                "month": 1,
                "date_range": {"start": "2026-01-31", "end": "2026-02-27"},
                "timeframe": "current",
            },
            "amount": {
                "gross_income_clp": 1000000,
                "taxable_income_clp": 1000000,
                "total_discounts_clp": 170000,
                "net_pay_clp": 830000,
                "net_pay_uf": None,
                "net_pay_usd": None,
                "net_pay_eur": None,
                "increase": None,
                "net_pay_clp_today": None,
            },
        },
        {
            "id": None,
            "employer": None,
            "period": {
                "year": 2026,
                "month": 2,
                "date_range": {"start": "2026-02-28", "end": "2026-03-30"},
                "timeframe": "future",
            },
            "amount": {
                "gross_income_clp": None,
                "taxable_income_clp": None,
                "total_discounts_clp": None,
                "net_pay_clp": None,
                "net_pay_uf": None,
                "net_pay_usd": None,
                "net_pay_eur": None,
                "increase": 0.0,
                "net_pay_clp_today": None,
            },
        },
    ]
    assert detail_response.status_code == 200
    assert detail_response.json() == {
        "id": 7,
        "employer": {"id": 1, "name": "ACME"},
        "period": {
            "year": 2026,
            "month": 1,
            "date_range": {"start": "2026-01-31", "end": "2026-02-27"},
            "timeframe": "current",
        },
        "amount": {
            "gross_income_clp": 1000000,
            "taxable_income_clp": 1000000,
            "total_discounts_clp": 170000,
            "net_pay_clp": 830000,
            "net_pay_uf": None,
            "net_pay_usd": None,
            "net_pay_eur": None,
            "increase": 25.0,  # (1500000-1200000)/1200000 * 100
            "net_pay_clp_today": None,  # only computed for timeframe == previous
        },
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
        no_params_response = client.get("/payroll")
        explicit_response = client.get(
            "/payroll",
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
        response = client.get("/payroll", params={"future_months": 13})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_payroll_period_range_rejects_negative_previous_months() -> None:
    """A negative previous_months is a 422 validation error."""
    app.dependency_overrides[get_payroll_queries] = lambda: FakePayrollQueries()
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.get("/payroll", params={"previous_months": -1})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_payroll_detail_endpoint_surfaces_not_found() -> None:
    """Test payroll detail endpoint surfaces not found."""

    class ErrorPayrollQueries:
        """Represent the error payroll queries."""

        async def get_period_range(
            self, period_id: int
        ) -> PayrollPeriodRangeContextDTO:
            """Get period range."""
            raise PayrollPeriodNotFoundError("Payroll period 9 was not found.")

    app.dependency_overrides[get_payroll_queries] = lambda: ErrorPayrollQueries()
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.get("/payroll/9")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Payroll period 9 was not found."}


def _default_increase_current_period(**overrides: object) -> PayrollPeriodRangeDTO:
    """Build the baseline 2026-01 'current' period shared by _compute_increase tests."""
    fields: dict[str, object] = {
        "net_pay_clp": None,
        "salary_base": Decimal("1000000"),
        "worked_days": 30,
    }
    fields.update(overrides)
    net_pay_clp = fields.pop("net_pay_clp")
    return _make_period_range(
        2026, 1, date(2026, 1, 31), date(2026, 2, 27), net_pay_clp, **fields
    )


def _default_increase_predecessor_period(**overrides: object) -> PayrollPeriodRangeDTO:
    """Build the baseline 2025-12 'predecessor' period for _compute_increase tests."""
    fields: dict[str, object] = {
        "net_pay_clp": None,
        "salary_base": Decimal("1000000"),
        "worked_days": 30,
    }
    fields.update(overrides)
    net_pay_clp = fields.pop("net_pay_clp")
    return _make_period_range(
        2025, 12, date(2025, 12, 31), date(2026, 1, 30), net_pay_clp, **fields
    )


def test_compute_increase_returns_positive_pct_when_normalized_salary_rose() -> None:
    """Increase is the percentage change when (salary_base/worked_days)*30 grew."""
    current = _default_increase_current_period(
        net_pay_clp=Decimal("830000"), salary_base=Decimal("1200000")
    )
    predecessor = _default_increase_predecessor_period(net_pay_clp=Decimal("780000"))
    assert _compute_increase(current, predecessor) == Decimal("20.00")


def test_compute_increase_returns_negative_pct_when_normalized_salary_fell() -> None:
    """Increase is the percentage change (negative) when normalized salary dropped."""
    current = _default_increase_current_period(net_pay_clp=Decimal("780000"))
    predecessor = _default_increase_predecessor_period(
        net_pay_clp=Decimal("830000"), salary_base=Decimal("1200000")
    )
    assert _compute_increase(current, predecessor) == Decimal("-16.67")


def test_compute_increase_returns_none_when_predecessor_has_no_salary() -> None:
    """Increase is null when predecessor lacks salary_base data."""
    current = _default_increase_current_period()
    predecessor = _default_increase_predecessor_period(
        inferred=True, salary_base=None, worked_days=None
    )
    assert _compute_increase(current, predecessor) is None
    assert _compute_increase(current, None) is None


def test_compute_increase_accounts_for_worked_days_normalization() -> None:
    """Normalized salary comparison uses worked_days, not raw salary_base."""
    # Period with fewer worked_days but same salary_base should appear higher normalized
    current = _default_increase_current_period(worked_days=25)  # (1000000/25)*30=1.2M
    predecessor = _default_increase_predecessor_period()  # (1000000/30)*30 = 1000000
    assert _compute_increase(current, predecessor) == Decimal("20.00")


def test_compute_increase_returns_none_for_zero_salary_predecessor() -> None:
    """A zero-salary predecessor baseline makes percent change undefined."""
    current = _default_increase_current_period()
    predecessor = _default_increase_predecessor_period(salary_base=Decimal("0"))
    assert _compute_increase(current, predecessor) is None


def _default_current_net_pay_period(**overrides: object) -> PayrollPeriodRangeDTO:
    """Build the baseline 2026-09 'current' period shared by net_pay_clp_today tests.

    net_pay_clp=3,000,000, net_pay_uf=75 (today's UF/CLP rate is 40,000),
    salary_base=1,500,000, worked_days=30 -- any field a given test needs
    to flex (salary_base, net_pay_uf, worked_days, net_pay_clp...) is
    passed as a keyword override instead of a whole new literal block.
    """
    fields: dict[str, object] = {
        "net_pay_clp": Decimal("3000000"),
        "is_current": True,
        "net_pay_uf": Decimal("75"),
        "salary_base": Decimal("1500000"),
        "worked_days": 30,
    }
    fields.update(overrides)
    net_pay_clp = fields.pop("net_pay_clp")
    return _make_period_range(
        2026, 9, date(2026, 9, 29), date(2026, 10, 28), net_pay_clp, **fields
    )


def _default_previous_net_pay_period(**overrides: object) -> PayrollPeriodRangeDTO:
    """Build the baseline 2025-09 'previous' period shared by net_pay_clp_today tests.

    net_pay_clp=1,000,000, net_pay_uf=20 (that period's own UF/CLP rate
    was 50,000), salary_base=1,500,000, worked_days=30 -- same
    override-only-what-you-need approach as _default_current_net_pay_period().
    """
    fields: dict[str, object] = {
        "net_pay_clp": Decimal("1000000"),
        "net_pay_uf": Decimal("20"),
        "salary_base": Decimal("1500000"),
        "worked_days": 30,
    }
    fields.update(overrides)
    net_pay_clp = fields.pop("net_pay_clp")
    return _make_period_range(
        2025, 9, date(2025, 9, 27), date(2025, 10, 28), net_pay_clp, **fields
    )


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
    current = _default_current_net_pay_period(salary_base=Decimal("1650000"))
    previous = _default_previous_net_pay_period(fixed_uf_clp=Decimal("50000"))
    result = _compute_net_pay_clp_today(previous, current)
    assert result == Decimal("1115000")


def test_compute_net_pay_clp_today_defaults_fixed_uf_clp_to_zero() -> None:
    """No HEALTH_ADDITIONAL_UF item for the period -> fixed_uf_clp defaults to 0.

    With no UF-driven discount at all, the whole net_pay is "scalable" and
    is scaled purely by the real salary_base ratio (here, flat: 1.0) --
    result equals the historical net_pay_clp unchanged.
    """
    current = _default_current_net_pay_period()
    previous = _default_previous_net_pay_period()
    assert _compute_net_pay_clp_today(previous, current) == Decimal("1000000")


def test_compute_net_pay_clp_today_returns_none_without_current() -> None:
    """No current period resolved (e.g. none found at all) -> None."""
    previous = _default_previous_net_pay_period()
    assert _compute_net_pay_clp_today(previous, None) is None


def test_compute_net_pay_clp_today_returns_none_without_salary_or_uf_data() -> None:
    """Missing net_pay_uf, salary_base or worked_days on either side -> None."""
    current = _default_current_net_pay_period()
    for missing_field in ("net_pay_uf", "salary_base", "worked_days"):
        previous = _default_previous_net_pay_period(**{missing_field: None})
        assert _compute_net_pay_clp_today(previous, current) is None


def test_compute_net_pay_clp_today_returns_none_without_current_rate_ingredients() -> (
    None
):
    """current.net_pay_clp/net_pay_uf/salary_base missing or zero -> None."""
    previous = _default_previous_net_pay_period()
    for override in (
        {"net_pay_clp": None},
        {"net_pay_uf": None},
        {"net_pay_uf": Decimal("0")},
        {"salary_base": None},
        {"worked_days": None},
    ):
        current = _default_current_net_pay_period(**override)
        assert _compute_net_pay_clp_today(previous, current) is None


def test_compute_net_pay_clp_today_returns_none_for_zero_normalized_salary() -> None:
    """A zero historical normalized salary_base makes the ratio undefined."""
    current = _default_current_net_pay_period()
    previous = _default_previous_net_pay_period(salary_base=Decimal("0"))
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
        response = client.get("/payroll")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data[0]["period"]["timeframe"] == "previous"
    assert data[0]["amount"]["increase"] is None  # no predecessor in window
    assert data[1]["period"]["timeframe"] == "previous"
    assert data[1]["amount"]["increase"] == 20.0  # (1200000-1000000)/1000000 * 100
    assert data[2]["period"]["timeframe"] == "current"
    assert data[2]["amount"]["increase"] == 25.0  # (1500000-1200000)/1200000 * 100
    assert data[3]["period"]["timeframe"] == "future"
    assert data[3]["amount"]["increase"] == 0.0


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
        response = client.get("/payroll")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    # Lookback is NOT in the response
    assert len(data) == 3
    assert data[0]["period"]["timeframe"] == "previous"
    assert data[0]["period"]["month"] == 11
    assert data[0]["amount"]["increase"] == 20.0  # vs lookback: +20%
    assert data[1]["period"]["timeframe"] == "current"
    assert data[1]["amount"]["increase"] == 0.0  # 1200000 == 1200000 -> 0% change
    assert data[2]["period"]["timeframe"] == "future"


@pytest.mark.asyncio
async def test_payroll_detail_handler_maps_value_errors() -> None:
    """Test payroll detail handler maps value errors."""

    class ErrorPayrollQueries:
        """Represent the error payroll queries."""

        async def get_period_range(
            self, period_id: int
        ) -> PayrollPeriodRangeContextDTO:
            """Get period range."""
            raise PayrollPeriodNotFoundError("Payroll period 9 was not found.")

    with pytest.raises(HTTPException, match="Payroll period 9 was not found."):
        await get_payroll_period(period_id=9, queries=ErrorPayrollQueries())
