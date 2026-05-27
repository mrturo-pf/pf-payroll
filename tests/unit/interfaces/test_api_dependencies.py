"""Tests for API dependency injection."""

from unittest.mock import MagicMock


from payroll.interfaces.api.dependencies import (
    get_complementary_insurance_repository,
)


def test_get_complementary_insurance_repository() -> None:
    """Test getting complementary insurance repository."""
    session = MagicMock()
    repository = get_complementary_insurance_repository(session)
    assert repository is not None
