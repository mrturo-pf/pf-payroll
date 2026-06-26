"""Tests for API dependency injection."""

from unittest.mock import MagicMock

from payroll.application.use_cases.deflate_amounts import DeflateAmounts
from payroll.application.use_cases.process_imported_payroll_periods import (
    ProcessImportedPayrollPeriods,
)
from payroll.interfaces.api.dependencies import (
    get_complementary_insurance_repository,
    get_deflate_amounts_use_case,
    get_process_imported_payroll_periods_use_case,
)


def test_get_complementary_insurance_repository() -> None:
    """Test getting complementary insurance repository."""
    session = MagicMock()
    repository = get_complementary_insurance_repository(session)
    assert repository is not None


def test_get_process_imported_payroll_periods_use_case_is_instantiable() -> None:
    """Test that the process-imported-payroll use case can be created."""
    repository = MagicMock()
    ci_repository = MagicMock()
    use_case = get_process_imported_payroll_periods_use_case(
        repository=repository,
        complementary_insurance_repository=ci_repository,
    )
    assert isinstance(use_case, ProcessImportedPayrollPeriods)


def test_get_deflate_amounts_use_case_is_instantiable() -> None:
    """Test that the deflate amounts use case can be created."""
    repository = MagicMock()
    use_case = get_deflate_amounts_use_case(repository=repository)
    assert isinstance(use_case, DeflateAmounts)
