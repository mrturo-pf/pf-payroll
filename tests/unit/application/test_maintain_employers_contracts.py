"""Tests for employer and employment-contract maintenance use cases."""

from unittest.mock import AsyncMock

import pytest

from payroll.application.dto import EmployerMaintenanceDTO
from payroll.application.errors import PayrollValidationError
from payroll.application.use_cases.maintain_employers_contracts import (
    DeleteEmployers,
    DeleteEmploymentContracts,
    MaintainEmployers,
    MaintainEmploymentContracts,
)
from unit.employer_contract_fixtures import (
    contract_command as contract,
    employer_command as employer,
)


@pytest.mark.asyncio
async def test_maintenance_use_cases_delegate_batches() -> None:
    """Delegate valid employer and contract batches."""
    repository = AsyncMock()
    repository.maintain_employers.return_value.employers = [employer(1)]
    repository.maintain_contracts.return_value.contracts = [contract(1)]

    assert (await MaintainEmployers(repository).execute([employer()])).employers
    assert (
        await MaintainEmploymentContracts(repository).execute([contract()])
    ).contracts
    repository.maintain_employers.assert_awaited_once()
    repository.maintain_contracts.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_case", "payload", "message"),
    [
        (MaintainEmployers, [], "employers must not be empty"),
        (MaintainEmploymentContracts, [], "contracts must not be empty"),
    ],
)
async def test_maintenance_use_cases_reject_empty_batches(
    use_case: type, payload: list[object], message: str
) -> None:
    """Reject empty mutation batches."""
    with pytest.raises(PayrollValidationError, match=message):
        await use_case(AsyncMock()).execute(payload)


@pytest.mark.asyncio
async def test_maintenance_use_cases_reject_bad_ids() -> None:
    """Reject non-positive and duplicate IDs."""
    repository = AsyncMock()
    for commands in ([employer(0)], [employer(1), employer(1)], [contract(-1)]):
        use_case = (
            MaintainEmployers(repository)
            if isinstance(commands[0], EmployerMaintenanceDTO)
            else MaintainEmploymentContracts(repository)
        )
        with pytest.raises(PayrollValidationError):
            await use_case.execute(commands)


@pytest.mark.asyncio
async def test_delete_use_cases_validate_and_delegate() -> None:
    """Validate deletion IDs and delegate valid batches."""
    repository = AsyncMock()
    await DeleteEmployers(repository).execute([1, 2])
    await DeleteEmploymentContracts(repository).execute([3])
    repository.delete_employers.assert_awaited_once_with([1, 2])
    repository.delete_contracts.assert_awaited_once_with([3])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_case", "field"),
    [(DeleteEmployers, "employer_ids"), (DeleteEmploymentContracts, "contract_ids")],
)
async def test_delete_use_cases_reject_invalid_lists(
    use_case: type, field: str
) -> None:
    """Reject empty, non-positive, and duplicate deletion IDs."""
    for ids in ([], [0], [1, 1]):
        with pytest.raises(PayrollValidationError, match=field):
            await use_case(AsyncMock()).execute(ids)
