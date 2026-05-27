"""Tests for test reference data queries."""

from decimal import Decimal

import pytest

from payroll.application.use_cases.reference_data import ReferenceDataQueries
from helpers.reference_data import ReferenceDataStubMixin


class StubReferenceDataRepository(ReferenceDataStubMixin):
    """Test double for Reference Data Repository."""


@pytest.mark.asyncio
async def test_reference_data_queries_delegate_to_repository() -> None:
    """Test reference data queries delegate to repository."""
    repository = StubReferenceDataRepository()
    queries = ReferenceDataQueries(repository)

    assert [item.code for item in await queries.list_currencies()] == ["CLP"]
    assert [item.code for item in await queries.list_pension_institutions()] == [
        "AFP_UNO"
    ]
    assert [item.code for item in await queries.list_health_institutions()] == [
        "FONASA"
    ]
    assert [item.id for item in await queries.list_pension_plans()] == [1]
    assert [item.id for item in await queries.list_health_plans()] == [2]
    assert repository.include_inactive_health_plans is False
    assert [item.cap_type for item in await queries.list_contribution_caps()] == [
        "pension_health"
    ]
    assert [item.code for item in await queries.list_payroll_concepts()] == [
        "SALARY_BASE"
    ]
    assert [
        item.lower_bound_utm for item in await queries.list_income_tax_brackets()
    ] == [Decimal("0")]
    assert repository.include_inactive_health_institutions is False


@pytest.mark.asyncio
async def test_reference_data_queries_forward_include_inactive_flags() -> None:
    """Test reference data queries forward include_inactive flags."""
    repository = StubReferenceDataRepository()
    queries = ReferenceDataQueries(repository)

    await queries.list_health_institutions(include_inactive=True)
    await queries.list_health_plans(include_inactive=True)

    assert repository.include_inactive_health_institutions is True
    assert repository.include_inactive_health_plans is True
