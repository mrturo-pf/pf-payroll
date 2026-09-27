"""Tests for payroll route response-mapping helpers."""

from datetime import date
from decimal import Decimal

from payroll.application.dto import (
    ImportedContributionValidationDTO,
    ImportedPayrollPeriodDTO,
)
from payroll.domain.contributions import EmploymentContractKind
from payroll.interfaces.api.routes.payroll import (
    ImportedContributionValidationRead,
    build_reconciliation_conflict_detail,
    to_imported_contribution_validation_read,
)


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
        "status": "actual",
        "employment_contract_kind": EmploymentContractKind.INDEFINITE,
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
