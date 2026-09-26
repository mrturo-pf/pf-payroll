"""Tests for the shared import-reconciliation-conflict detection helpers."""

from datetime import date
from decimal import Decimal

from payroll.application.dto import (
    ImportedComplementaryInsuranceValidationDTO,
    ImportedContributionValidationDTO,
    ImportedPayrollPeriodDTO,
)
from payroll.application.services.import_reconciliation import (
    is_import_fully_validated,
    period_has_reconciliation_conflict,
)
from payroll.domain.contributions import EmploymentContractKind


def _make_period(**overrides: object) -> ImportedPayrollPeriodDTO:
    """Build a minimal, otherwise-clean ImportedPayrollPeriodDTO for these tests."""
    defaults: dict[str, object] = {
        "id": 1,
        "employer": "ACME",
        "period_year": 2026,
        "period_month": 8,
        "payment_date": date(2026, 8, 31),
        "status": "actual",
        "employment_contract_kind": EmploymentContractKind.INDEFINITE,
        "item_count": 1,
    }
    defaults.update(overrides)
    return ImportedPayrollPeriodDTO(**defaults)


def test_no_conflict_for_a_clean_period() -> None:
    """No warnings anywhere -> no conflict, fully validated."""
    period = _make_period()

    assert period_has_reconciliation_conflict(period) is False
    assert is_import_fully_validated([period]) is True


def test_no_conflict_when_net_pay_is_pending() -> None:
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

    assert period_has_reconciliation_conflict(period) is False
    assert is_import_fully_validated([period]) is True


def test_conflict_on_genuine_net_pay_mismatch() -> None:
    """A net_pay_warning alongside a real expected_net_pay_clp is a genuine conflict."""
    period = _make_period(
        net_pay_warning=(
            "Declared net_pay does not match the fully computed payroll "
            "totals. Difference: 5000 CLP."
        ),
        expected_net_pay_clp=Decimal("945000"),
    )

    assert period_has_reconciliation_conflict(period) is True
    assert is_import_fully_validated([period]) is False


def test_no_conflict_when_contribution_validation_is_pending() -> None:
    """A pending contribution_validation.warning does not count as a conflict.

    Mirrors build_imported_contribution_validation()'s own pending case
    (computed=None, e.g. plans not assigned yet): expected_pension_base_clp
    stays None, so this must not be treated as a conflict.
    """
    validation = ImportedContributionValidationDTO(
        declared_pension_base_clp=Decimal("100000"),
        warning=(
            "Contribution values will be reconciled after pension and "
            "health plans are assigned."
        ),
    )
    period = _make_period(contribution_validation=validation)

    assert period_has_reconciliation_conflict(period) is False


def test_conflict_on_genuine_contribution_mismatch() -> None:
    """A contribution_validation.warning alongside expected_* fields is real."""
    validation = ImportedContributionValidationDTO(
        declared_pension_base_clp=Decimal("100000"),
        expected_pension_base_clp=Decimal("50000"),
        pension_base_difference_clp=Decimal("50000"),
        warning="Imported contribution totals do not match.",
    )
    period = _make_period(contribution_validation=validation)

    assert period_has_reconciliation_conflict(period) is True


def test_no_conflict_when_contribution_validation_has_no_warning() -> None:
    """A present-but-clean contribution_validation is not a conflict."""
    validation = ImportedContributionValidationDTO(warning=None)
    period = _make_period(contribution_validation=validation)

    assert period_has_reconciliation_conflict(period) is False


def test_no_conflict_on_pending_complementary_insurance_warning() -> None:
    """A pending-prefixed complementary insurance warning is not a conflict."""
    period = _make_period(
        complementary_insurance_validation=ImportedComplementaryInsuranceValidationDTO(
            warnings=[
                "Complementary insurance validation pending: no economic index found."
            ]
        )
    )

    assert period_has_reconciliation_conflict(period) is False


def test_conflict_on_non_pending_complementary_insurance_warning() -> None:
    """A non-pending complementary_insurance_validation.warnings is a conflict."""
    period = _make_period(
        complementary_insurance_validation=ImportedComplementaryInsuranceValidationDTO(
            warnings=["Plan mismatch."]
        )
    )

    assert period_has_reconciliation_conflict(period) is True


def test_no_conflict_when_complementary_insurance_warnings_empty() -> None:
    """A present-but-empty complementary_insurance_validation is not a conflict."""
    period = _make_period(
        complementary_insurance_validation=ImportedComplementaryInsuranceValidationDTO(
            warnings=[]
        )
    )

    assert period_has_reconciliation_conflict(period) is False


def test_is_import_fully_validated_false_if_any_period_conflicts() -> None:
    """One conflicting period among several clean ones fails the whole batch."""
    clean = _make_period(id=1)
    conflicting = _make_period(
        id=2,
        net_pay_warning="Declared net_pay does not match. Difference: 1 CLP.",
        expected_net_pay_clp=Decimal("1"),
    )

    assert is_import_fully_validated([clean, conflicting]) is False


def test_is_import_fully_validated_true_for_empty_periods() -> None:
    """No periods at all trivially reconciles cleanly."""
    assert is_import_fully_validated([]) is True
