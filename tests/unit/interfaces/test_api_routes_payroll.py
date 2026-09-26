"""Tests for payroll route response-mapping helpers."""

from datetime import date
from decimal import Decimal

from payroll.application.dto import (
    ImportedComplementaryInsuranceValidationDTO,
    ImportedContributionValidationDTO,
)
from payroll.domain.contributions import EmploymentContractKind
from payroll.interfaces.api.routes.payroll import (
    ImportedContributionValidationRead,
    ImportedPeriodRead,
    UnresolvedRowWarning,
    _is_fully_validated,
    to_imported_contribution_validation_read,
)


def _make_period(**overrides: object) -> ImportedPeriodRead:
    """Build a minimal, otherwise-clean ImportedPeriodRead for these tests."""
    defaults: dict[str, object] = {
        "id": 1,
        "employer": "ACME",
        "period_year": 2026,
        "period_month": 8,
        "payment_date": date(2026, 8, 31),
        "status": "actual",
        "employment_contract_kind": EmploymentContractKind.INDEFINITE,
        "item_count": 1,
        "net_pay_warning": None,
        "contribution_validation": None,
        "complementary_insurance_validation": None,
    }
    defaults.update(overrides)
    return ImportedPeriodRead(**defaults)


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


def test_is_fully_validated_true_for_a_clean_period() -> None:
    """No unresolved rows, no warnings anywhere -> validated."""
    period = _make_period()

    assert _is_fully_validated([period], []) is True


def test_is_fully_validated_false_on_unresolved_rows() -> None:
    """Any unresolved row fails validation, even with otherwise clean periods."""
    period = _make_period()
    unresolved = [UnresolvedRowWarning(row_index=0, amount_clp="1000")]

    assert _is_fully_validated([period], unresolved) is False


def test_is_fully_validated_true_when_net_pay_is_pending() -> None:
    """A net_pay_warning with no expected_net_pay_clp yet is pending, not a conflict.

    This is the normal shape for a freshly-imported projected period: there
    is nothing wrong, simply nothing computed yet to compare against.
    """
    period = _make_period(
        net_pay_warning=(
            "Declared net_pay will be reconciled after computed contributions "
            "and income tax are generated."
        ),
        expected_net_pay_clp=None,
    )

    assert _is_fully_validated([period], []) is True


def test_is_fully_validated_false_on_genuine_net_pay_conflict() -> None:
    """A net_pay_warning alongside a real expected_net_pay_clp is a genuine conflict."""
    period = _make_period(
        net_pay_warning=(
            "Declared net_pay does not match the fully computed payroll "
            "totals. Difference: 5000 CLP."
        ),
        expected_net_pay_clp=Decimal("945000"),
    )

    assert _is_fully_validated([period], []) is False


def test_is_fully_validated_true_when_contribution_validation_is_pending() -> None:
    """A pending contribution_validation.warning does not fail validation.

    Mirrors build_imported_contribution_validation()'s own pending case
    (computed=None, e.g. plans not assigned yet): expected_pension_base_clp
    stays None, so this must not be treated as a conflict.
    """
    validation = to_imported_contribution_validation_read(
        ImportedContributionValidationDTO(
            declared_pension_base_clp=Decimal("100000"),
            warning=(
                "Contribution values will be reconciled after pension and "
                "health plans are assigned."
            ),
        )
    )
    period = _make_period(contribution_validation=validation)

    assert _is_fully_validated([period], []) is True


def test_is_fully_validated_false_on_genuine_contribution_conflict() -> None:
    """A contribution_validation.warning alongside expected_* fields is real."""
    validation = to_imported_contribution_validation_read(
        ImportedContributionValidationDTO(
            declared_pension_base_clp=Decimal("100000"),
            expected_pension_base_clp=Decimal("50000"),
            pension_base_difference_clp=Decimal("50000"),
            warning="Imported contribution totals do not match.",
        )
    )
    period = _make_period(contribution_validation=validation)

    assert _is_fully_validated([period], []) is False


def test_is_fully_validated_true_when_contribution_validation_has_no_warning() -> None:
    """A present-but-clean contribution_validation does not fail validation."""
    validation = to_imported_contribution_validation_read(
        ImportedContributionValidationDTO(warning=None)
    )
    period = _make_period(contribution_validation=validation)

    assert _is_fully_validated([period], []) is True


def test_is_fully_validated_true_on_pending_complementary_insurance_warning() -> None:
    """A pending-prefixed complementary insurance warning does not fail validation."""
    period = _make_period(
        complementary_insurance_validation=(
            ImportedComplementaryInsuranceValidationDTO(
                warnings=[
                    "Complementary insurance validation pending: "
                    "no economic index found."
                ]
            )
        )
    )

    assert _is_fully_validated([period], []) is True


def test_is_fully_validated_false_on_complementary_insurance_warning() -> None:
    """A non-pending complementary_insurance_validation.warnings fails validation."""
    period = _make_period(
        complementary_insurance_validation=(
            ImportedComplementaryInsuranceValidationDTO(warnings=["Plan mismatch."])
        )
    )

    assert _is_fully_validated([period], []) is False


def test_is_fully_validated_true_when_complementary_insurance_warnings_empty() -> None:
    """A present-but-empty complementary_insurance_validation does not fail it."""
    period = _make_period(
        complementary_insurance_validation=(
            ImportedComplementaryInsuranceValidationDTO(warnings=[])
        )
    )

    assert _is_fully_validated([period], []) is True
