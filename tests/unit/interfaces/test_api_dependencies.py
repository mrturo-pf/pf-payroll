"""Tests for API dependency injection."""

from unittest.mock import MagicMock

import pytest

from helpers.db_fakes import assert_get_transactional_session_lifecycle
from payroll.application.use_cases.deflate_amounts import DeflateAmounts
from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.application.use_cases.preview_pdf_import import PreviewPdfImport
from payroll.application.use_cases.process_imported_payroll_periods import (
    ProcessImportedPayrollPeriods,
)
from payroll.interfaces.api import dependencies
from payroll.interfaces.api.dependencies import (
    get_complementary_insurance_repository,
    get_complementary_insurance_repository_for_rows_import,
    get_deflate_amounts_use_case,
    get_import_payroll_use_case_for_rows_import,
    get_payroll_repository_for_rows_import,
    get_preview_pdf_import_use_case,
    get_process_imported_payroll_periods_use_case,
    get_process_imported_payroll_periods_use_case_for_rows_import,
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


def test_get_preview_pdf_import_use_case_is_instantiable() -> None:
    """Test that the preview pdf import use case can be created (no repo)."""
    use_case = get_preview_pdf_import_use_case()
    assert isinstance(use_case, PreviewPdfImport)


@pytest.mark.asyncio
async def test_get_transactional_session_manages_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_transactional_session() delegates its lifecycle correctly."""
    await assert_get_transactional_session_lifecycle(monkeypatch, dependencies)


def test_get_payroll_repository_for_rows_import() -> None:
    """Test building a payroll repository bound to a transactional scope."""
    scope = MagicMock(session=MagicMock())
    repository = get_payroll_repository_for_rows_import(scope)
    assert repository is not None


def test_get_complementary_insurance_repository_for_rows_import() -> None:
    """Test building a complementary insurance repository from the scope."""
    scope = MagicMock(session=MagicMock())
    repository = get_complementary_insurance_repository_for_rows_import(scope)
    assert repository is not None


def test_get_import_payroll_use_case_for_rows_import_is_instantiable() -> None:
    """Test that the rows-import use case can be created."""
    repository = MagicMock()
    use_case = get_import_payroll_use_case_for_rows_import(repository)
    assert isinstance(use_case, ImportPayroll)


def test_get_process_imported_payroll_periods_use_case_for_rows_import() -> None:
    """Test that the rows-import post-processing use case can be created."""
    repository = MagicMock()
    ci_repository = MagicMock()
    use_case = get_process_imported_payroll_periods_use_case_for_rows_import(
        repository=repository,
        complementary_insurance_repository=ci_repository,
    )
    assert isinstance(use_case, ProcessImportedPayrollPeriods)
