"""Shared pytest fixtures for unit/application test modules."""

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_payroll_repository() -> AsyncMock:
    """Create a mock payroll repository."""
    return AsyncMock()


@pytest.fixture
def mock_complementary_insurance_repository() -> AsyncMock:
    """Create a mock complementary insurance repository."""
    return AsyncMock()


@pytest.fixture
def mock_market_data_repository() -> AsyncMock:
    """Create a mock market data repository."""
    return AsyncMock()
