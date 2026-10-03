"""Tests for payroll health-plan snapshot replacement."""

import pytest

from payroll.infrastructure.db.repositories.payroll_repository import (
    SqlAlchemyPayrollRepository,
)

from test_payroll_repository import FakeResult, FakeSession


@pytest.mark.asyncio
async def test_replacing_health_snapshots_accepts_empty_plan_list() -> None:
    """Replacement can clear all health snapshots for a period."""
    repository = SqlAlchemyPayrollRepository(FakeSession([FakeResult()]))  # type: ignore[arg-type]

    await repository._replace_period_health_plan_snapshots(  # noqa: SLF001
        period_id=1, health_plan_ids=[]
    )

    assert repository._session.added == []  # noqa: SLF001
