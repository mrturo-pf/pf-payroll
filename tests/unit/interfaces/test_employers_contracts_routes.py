"""Tests for employer and contract maintenance routes."""

from datetime import date
from unittest.mock import AsyncMock
from typing import Any

import pytest
from fastapi import HTTPException

from payroll.application.dto import (
    ContractMaintenanceResultDTO,
    EmployerMaintenanceDTO,
    EmployerMaintenanceResultDTO,
    EmployerReadDTO,
    EmploymentContractMaintenanceDTO,
    EmploymentContractNestedReadDTO,
    EmploymentContractReadDTO,
    EmployerReferenceDTO,
    FirstIncreasePeriodReadDTO,
    IncreaseReadDTO,
    PaymentDateReadDTO,
)
from payroll.application.errors import PayrollConflictError
from payroll.interfaces.api.routes.employers_contracts import (
    ContractMaintenanceItem,
    ContractMaintenanceRequest,
    EmployerMaintenanceItem,
    EmployerMaintenanceRequest,
    IdBatchRequest,
    delete_contracts,
    delete_employers,
    list_contracts,
    list_employers,
    maintain_contracts,
    maintain_employers,
)


class Scope:
    """Record transaction resolution."""

    def __init__(self) -> None:
        """Initialize the scope."""
        self.modes: list[str] = []

    async def resolve(self, mode: str) -> None:
        """Record resolution."""
        self.modes.append(mode)


def employer_dto() -> EmployerMaintenanceDTO:
    """Build an employer DTO."""
    return EmployerMaintenanceDTO(1, "ACME", None, "CL")


def contract_dto() -> EmploymentContractMaintenanceDTO:
    """Build a contract DTO."""
    return EmploymentContractMaintenanceDTO(2, 1, date(2026, 1, 1), None, True, None)


@pytest.mark.asyncio
async def test_list_routes_return_repository_items() -> None:
    """List routes map DTOs to response models."""
    repository = AsyncMock()
    repository.list_employers.return_value = [
        EmployerReadDTO(
            id=1,
            name="ACME",
            tax_id=None,
            country_code="CL",
            increase=IncreaseReadDTO(
                frequency=12,
                first_increase_period=FirstIncreasePeriodReadDTO(2026, 1),
            ),
            payment_date=PaymentDateReadDTO(
                "last_business_day_of_month",
                0,
                None,
                0,
                0,
                False,
                "previous_business_day",
            ),
            contracts=[
                EmploymentContractNestedReadDTO(
                    2, date(2026, 1, 1), None, True, None, True
                )
            ],
        )
    ]
    repository.list_contracts.return_value = [
        EmploymentContractReadDTO(
            2,
            EmployerReferenceDTO(1, "ACME"),
            date(2026, 1, 1),
            None,
            True,
            None,
            True,
        )
    ]
    employers = await list_employers(repository)
    assert employers[0].contracts[0].is_in_effect
    assert employers[0].payment_date.rule == "last_business_day_of_month"
    assert (await list_contracts(1, repository))[0].is_in_effect


@pytest.mark.asyncio
async def test_maintain_routes_commit_batches() -> None:
    """Successful mutation routes resolve commit."""
    employer_use_case = AsyncMock()
    contract_use_case = AsyncMock()
    employer_use_case.execute.return_value = EmployerMaintenanceResultDTO(
        [employer_dto()]
    )
    contract_use_case.execute.return_value = ContractMaintenanceResultDTO(
        [contract_dto()]
    )
    scope: Any = Scope()
    employer_payload = EmployerMaintenanceRequest(
        employers=[EmployerMaintenanceItem(name="ACME", country_code="CL")]
    )
    contract_payload = ContractMaintenanceRequest(
        contracts=[
            ContractMaintenanceItem(
                employer_id=1, started_at=date(2026, 1, 1), is_indefinite=True
            )
        ]
    )
    assert (await maintain_employers(employer_payload, scope, employer_use_case))[
        0
    ].id == 1  # type: ignore[arg-type]
    assert (await maintain_contracts(contract_payload, scope, contract_use_case))[
        0
    ].id == 2  # type: ignore[arg-type]
    contract_use_case = AsyncMock()
    contract_use_case.execute.side_effect = PayrollConflictError("blocked")
    with pytest.raises(HTTPException):
        await maintain_contracts(contract_payload, scope, contract_use_case)  # type: ignore[arg-type]

    """Mutation errors resolve validate and become HTTP errors."""
    use_case = AsyncMock()
    use_case.execute.side_effect = PayrollConflictError("blocked")
    scope: Any = Scope()
    payload = EmployerMaintenanceRequest(
        employers=[EmployerMaintenanceItem(name="ACME", country_code="CL")]
    )
    with pytest.raises(HTTPException) as error:
        await maintain_employers(payload, scope, use_case)  # type: ignore[arg-type]
    assert error.value.status_code == 409
    assert scope.modes == ["validate"]


@pytest.mark.asyncio
async def test_delete_routes_commit_and_rollback() -> None:
    """Delete routes resolve commit on success and validate on errors."""
    employer_use_case = AsyncMock()
    contract_use_case = AsyncMock()
    scope = Scope()
    payload = IdBatchRequest(ids=[1])
    await delete_employers(payload, scope, employer_use_case)  # type: ignore[arg-type]
    await delete_contracts(payload, scope, contract_use_case)  # type: ignore[arg-type]
    assert scope.modes == ["commit", "commit"]

    failing = AsyncMock()
    failing.execute.side_effect = PayrollConflictError("blocked")
    with pytest.raises(HTTPException):
        await delete_employers(payload, scope, failing)  # type: ignore[arg-type]
    with pytest.raises(HTTPException):
        await delete_contracts(payload, scope, failing)  # type: ignore[arg-type]
    assert scope.modes[-1] == "validate"
