"""Tests for payroll-period deletion."""

import pytest

from payroll.application.use_cases.delete_payroll_periods import DeletePayrollPeriods


class StubPayrollRepository:
    """Record deletion requests."""

    def __init__(self) -> None:
        """Initialize the stub."""
        self.deleted: list[int] | None = None

    async def delete_periods(self, period_ids: list[int]) -> None:
        """Record period IDs."""
        self.deleted = period_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("period_ids", "message"),
    [
        ([], "must not be empty"),
        ([0], "must be positive"),
        ([-1], "must be positive"),
        ([1, 1], "must not contain duplicates"),
        (list(range(1, 502)), "maximum of 500"),
    ],
)
async def test_delete_rejects_invalid_ids(period_ids: list[int], message: str) -> None:
    """Reject invalid bulk-delete inputs before persistence."""
    repository = StubPayrollRepository()

    with pytest.raises(ValueError, match=message):
        await DeletePayrollPeriods(repository).execute(period_ids)  # type: ignore[arg-type]

    assert repository.deleted is None


@pytest.mark.asyncio
async def test_delete_delegates_valid_ids() -> None:
    """Delegate a valid list to the repository."""
    repository = StubPayrollRepository()

    await DeletePayrollPeriods(repository).execute([481, 482])

    assert repository.deleted == [481, 482]
