"""Tests for payroll route response-mapping helpers."""

from decimal import Decimal

from payroll.application.dto import ImportedContributionValidationDTO
from payroll.interfaces.api.routes.payroll import (
    ImportedContributionValidationRead,
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

    This is the concrete behavior requested for POST /payroll/import and
    POST /payroll/import/rows responses: MoneyCLP overrides pydantic's
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
