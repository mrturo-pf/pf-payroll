"""Tests for payroll route response-mapping helpers."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from payroll.application.dto import (
    ImportedContributionValidationDTO,
    ImportedPayrollPeriodDTO,
    PayrollPeriodRangeContextDTO,
    PayrollPeriodRangeDTO,
)
from payroll.application.errors import PayrollValidationError
from payroll.interfaces.api.routes.payroll import (
    DeletePayrollPeriodsRequest,
    ImportPayrollPeriodRequest,
    ImportedContributionValidationRead,
    delete_payroll_period,
    delete_payroll_periods,
    _execute_delete_payroll_periods,
    _validate_period_ids,
    build_reconciliation_conflict_detail,
    to_imported_contribution_validation_read,
    to_payroll_period_read,
)


class _DeleteScope:
    """Capture transaction resolution."""

    def __init__(self) -> None:
        """Initialize the scope."""
        self.resolutions: list[str] = []

    async def resolve(self, mode: str) -> None:
        """Capture the requested resolution."""
        self.resolutions.append(mode)


class _DeleteUseCase:
    """Capture delete execution."""

    def __init__(self, error: Exception | None = None) -> None:
        """Initialize the use case."""
        self.error = error
        self.period_ids: list[int] | None = None

    async def execute(self, period_ids: list[int]) -> None:
        """Capture IDs or raise the configured error."""
        self.period_ids = period_ids
        if self.error is not None:
            raise self.error


def _period_request(period_id: int | None) -> ImportPayrollPeriodRequest:
    """Build a minimal period request for ID validation."""
    return ImportPayrollPeriodRequest(
        period_id=period_id,
        employer="ACME",
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        rows=[],
    )


def test_validate_period_ids_accepts_null_and_positive_ids() -> None:
    """Accept insert blocks and positive update IDs."""
    _validate_period_ids([_period_request(None), _period_request(481)])


@pytest.mark.parametrize("period_id", [0, -1])
def test_validate_period_ids_rejects_non_positive_ids(period_id: int) -> None:
    """Reject non-positive update IDs."""
    with pytest.raises(ValueError, match="must be positive"):
        _validate_period_ids([_period_request(period_id)])


def test_validate_period_ids_rejects_duplicates() -> None:
    """Reject duplicate update IDs."""
    with pytest.raises(ValueError, match="must not be duplicated"):
        _validate_period_ids([_period_request(481), _period_request(481)])


@pytest.mark.asyncio
async def test_execute_delete_resolves_commit() -> None:
    """Resolve a successful delete as commit."""
    scope = _DeleteScope()
    use_case = _DeleteUseCase()
    response = await _execute_delete_payroll_periods([481], scope, use_case)
    assert response.status_code == 204
    assert use_case.period_ids == [481]
    assert scope.resolutions == ["commit"]


@pytest.mark.asyncio
async def test_execute_delete_resolves_validate_on_error() -> None:
    """Resolve a failed delete as validate."""
    scope = _DeleteScope()
    use_case = _DeleteUseCase(PayrollValidationError("blocked"))
    with pytest.raises(HTTPException) as error:
        await _execute_delete_payroll_periods([481], scope, use_case)
    assert error.value.status_code == 400
    assert scope.resolutions == ["validate"]


@pytest.mark.asyncio
async def test_collection_delete_route_delegates_payload() -> None:
    """Pass collection IDs through the collection route."""
    scope = _DeleteScope()
    use_case = _DeleteUseCase()
    response = await delete_payroll_periods(
        DeletePayrollPeriodsRequest(period_ids=[481, 482]), scope, use_case
    )
    assert response.status_code == 204
    assert use_case.period_ids == [481, 482]


@pytest.mark.asyncio
async def test_single_delete_route_delegates_path_id() -> None:
    """Pass the path ID through the single-resource route."""
    scope = _DeleteScope()
    use_case = _DeleteUseCase()
    response = await delete_payroll_period(481, scope, use_case)
    assert response.status_code == 204
    assert use_case.period_ids == [481]


def test_to_imported_contribution_validation_read_returns_none_for_none() -> None:
    """A None DTO maps to None, matching ImportedPeriodRead's optional field."""
    assert to_imported_contribution_validation_read(None) is None


def test_to_imported_contribution_validation_read_maps_every_field() -> None:
    """Every *_clp field is copied over and the warning text is preserved."""
    amounts = {
        "declared_pension_base_clp": Decimal("367864.00"),
        "expected_pension_base_clp": Decimal("367864"),
        "pension_base_difference_clp": Decimal("0.00"),
        "declared_pension_additional_clp": Decimal("42672.00"),
        "expected_pension_additional_clp": Decimal("42672"),
        "pension_additional_difference_clp": Decimal("0.00"),
        "declared_health_base_clp": Decimal("257505.00"),
        "expected_health_base_clp": Decimal("257505"),
        "health_base_difference_clp": Decimal("0.00"),
        "declared_health_plan_additional_clp": Decimal("38013.00"),
        "expected_health_plan_additional_clp": Decimal("40000"),
        "health_plan_additional_difference_clp": Decimal("-1987.00"),
    }
    dto = ImportedContributionValidationDTO(
        **amounts, warning="Imported contribution totals do not match."
    )

    read = to_imported_contribution_validation_read(dto)

    assert read == ImportedContributionValidationRead(
        **amounts, warning="Imported contribution totals do not match."
    )


def test_imported_contribution_validation_read_serializes_amounts_as_json_numbers() -> (
    None
):
    """*_clp fields render as bare JSON numbers, not pydantic's default quoted str.

    This is the concrete behavior requested for POST /payroll/import/spreadsheet and
    POST /payroll/import/json responses: MoneyCLP overrides pydantic's
    default Decimal-to-string JSON serialization for these fields only.
    """
    read = ImportedContributionValidationRead(
        declared_pension_base_clp=Decimal("367864.00"),
        expected_health_plan_additional_clp=None,
        warning=None,
    )

    assert read.model_dump_json(include={"declared_pension_base_clp"}) == (
        '{"declared_pension_base_clp":367864}'
    )
    assert read.model_dump_json(include={"expected_health_plan_additional_clp"}) == (
        '{"expected_health_plan_additional_clp":null}'
    )


def _make_imported_period(**overrides: object) -> ImportedPayrollPeriodDTO:
    """Build a minimal ImportedPayrollPeriodDTO for detail-building tests."""
    defaults: dict[str, object] = {
        "id": 1,
        "employer": "ACME",
        "period_year": 2026,
        "period_month": 8,
        "payment_date": date(2026, 8, 28),
        "item_count": 1,
    }
    defaults.update(overrides)
    return ImportedPayrollPeriodDTO(**defaults)


def test_build_reconciliation_conflict_detail_includes_only_conflicting_periods() -> (
    None
):
    """The detail keeps the message plus only the periods that actually conflict.

    A 22-period import with one bad period should not force a caller to
    scan 21 clean ones to find it -- see conflicting_reconciliation_periods().
    """
    clean = _make_imported_period(id=1, period_month=1)
    conflicting = _make_imported_period(
        id=2,
        period_month=2,
        net_pay_warning="Declared net_pay does not match. Difference: 1 CLP.",
        expected_net_pay_clp=Decimal("1"),
    )

    detail = build_reconciliation_conflict_detail(
        "Cannot commit.", [clean, conflicting]
    )

    assert detail["message"] == "Cannot commit."
    periods = detail["conflicting_periods"]
    assert isinstance(periods, list)
    assert len(periods) == 1
    assert periods[0]["id"] is None  # rolled back -- see ImportedPeriodRead
    assert periods[0]["period_month"] == 2


def test_build_reconciliation_conflict_detail_empty_when_all_clean() -> None:
    """A fully clean batch never reaches this helper in practice, but stays safe."""
    detail = build_reconciliation_conflict_detail(
        "Cannot commit.", [_make_imported_period()]
    )

    assert detail == {"message": "Cannot commit.", "conflicting_periods": []}


def _make_period_range_dto(**overrides: object) -> PayrollPeriodRangeDTO:
    """Build a minimal real PayrollPeriodRangeDTO for /{period_id} tests."""
    defaults: dict[str, object] = {
        "period_year": 2026,
        "period_month": 3,
        "start_date": date(2026, 3, 28),
        "end_date": date(2026, 3, 28),
        "net_pay_clp": Decimal("1000000"),
        "is_current": False,
        "inferred": False,
        "period_id": 7,
    }
    defaults.update(overrides)
    return PayrollPeriodRangeDTO(**defaults)  # type: ignore[arg-type]


def test_to_payroll_period_read_resolves_previous_timeframe() -> None:
    """Target older than current -> timeframe 'previous', net_pay_clp_today computed.

    GET /payroll/{period_id}'s own timeframe resolution
    (_resolve_single_timeframe) is distinct from list_period_ranges()'s
    index-based one -- this is the one spot that exercises it against a
    target that is NOT the resolved current.
    """
    target = _make_period_range_dto(
        period_year=2026,
        period_month=2,
        start_date=date(2026, 2, 26),
        end_date=date(2026, 2, 26),
        period_id=6,
        salary_base=Decimal("1200000"),
        worked_days=30,
        fixed_uf_clp=Decimal("0"),
        net_pay_uf=Decimal("30.0"),
    )
    current = _make_period_range_dto(
        period_year=2026,
        period_month=3,
        start_date=date(2026, 3, 28),
        end_date=date(2026, 3, 28),
        period_id=7,
        is_current=True,
        salary_base=Decimal("1500000"),
        worked_days=30,
        fixed_uf_clp=Decimal("0"),
        net_pay_uf=Decimal("32.0"),
    )
    context = PayrollPeriodRangeContextDTO(
        target=target, predecessor=None, current=current
    )

    read = to_payroll_period_read(context)

    assert read.period.timeframe == "previous"
    assert read.amount.net_pay_clp_today is not None


def test_to_payroll_period_read_resolves_future_timeframe() -> None:
    """Target newer than current -> timeframe 'future', net_pay_clp_today stays None."""
    target = _make_period_range_dto(
        period_year=2026,
        period_month=4,
        start_date=date(2026, 4, 28),
        end_date=date(2026, 4, 28),
        period_id=8,
    )
    current = _make_period_range_dto(
        period_year=2026,
        period_month=3,
        start_date=date(2026, 3, 28),
        end_date=date(2026, 3, 28),
        period_id=7,
        is_current=True,
    )
    context = PayrollPeriodRangeContextDTO(
        target=target, predecessor=None, current=current
    )

    read = to_payroll_period_read(context)

    assert read.period.timeframe == "future"
    assert read.amount.net_pay_clp_today is None


def test_to_payroll_period_read_defaults_to_previous_without_any_current() -> None:
    """No period anywhere qualifies as current yet -> degrade to 'previous'."""
    target = _make_period_range_dto()
    context = PayrollPeriodRangeContextDTO(
        target=target, predecessor=None, current=None
    )

    read = to_payroll_period_read(context)

    assert read.period.timeframe == "previous"
    assert read.amount.net_pay_clp_today is None
