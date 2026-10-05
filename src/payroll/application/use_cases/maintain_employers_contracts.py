"""Use cases for employer and employment-contract maintenance."""

from payroll.application.dto import (
    ContractMaintenanceResultDTO,
    EmployerMaintenanceDTO,
    EmployerMaintenanceResultDTO,
    EmploymentContractMaintenanceDTO,
)
from payroll.application.errors import PayrollValidationError
from payroll.application.ports.repositories import PayrollRepository


class MaintainEmployers:
    """Create or update employers atomically."""

    def __init__(self, repository: PayrollRepository) -> None:
        """Initialize the use case."""
        self._repository = repository

    async def execute(
        self, employers: list[EmployerMaintenanceDTO]
    ) -> EmployerMaintenanceResultDTO:
        """Validate and persist an employer batch."""
        if not employers:
            raise PayrollValidationError("employers must not be empty.")
        _validate_batch_ids([item.id for item in employers])
        return await self._repository.maintain_employers(employers)


class MaintainEmploymentContracts:
    """Create or update employment contracts atomically."""

    def __init__(self, repository: PayrollRepository) -> None:
        """Initialize the use case."""
        self._repository = repository

    async def execute(
        self, contracts: list[EmploymentContractMaintenanceDTO]
    ) -> ContractMaintenanceResultDTO:
        """Validate and persist a contract batch."""
        if not contracts:
            raise PayrollValidationError("contracts must not be empty.")
        _validate_batch_ids([item.id for item in contracts])
        return await self._repository.maintain_contracts(contracts)


class DeleteEmployers:
    """Delete employers atomically after repository guard checks."""

    def __init__(self, repository: PayrollRepository) -> None:
        """Initialize the use case."""
        self._repository = repository

    async def execute(self, employer_ids: list[int]) -> None:
        """Delete employers."""
        _validate_delete_ids(employer_ids, "employer_ids")
        await self._repository.delete_employers(employer_ids)


class DeleteEmploymentContracts:
    """Delete contracts atomically after repository guard checks."""

    def __init__(self, repository: PayrollRepository) -> None:
        """Initialize the use case."""
        self._repository = repository

    async def execute(self, contract_ids: list[int]) -> None:
        """Delete contracts."""
        _validate_delete_ids(contract_ids, "contract_ids")
        await self._repository.delete_contracts(contract_ids)


def _validate_batch_ids(ids: list[int | None]) -> None:
    """Reject invalid or duplicated optional IDs."""
    present = [item_id for item_id in ids if item_id is not None]
    if any(item_id <= 0 for item_id in present):
        raise PayrollValidationError("IDs must be positive.")
    if len(present) != len(set(present)):
        raise PayrollValidationError("IDs must not contain duplicates.")


def _validate_delete_ids(ids: list[int], field_name: str) -> None:
    """Validate a non-empty unique positive deletion list."""
    if not ids:
        raise PayrollValidationError(f"{field_name} must not be empty.")
    if any(item_id <= 0 for item_id in ids):
        raise PayrollValidationError(f"{field_name} must contain positive IDs.")
    if len(ids) != len(set(ids)):
        raise PayrollValidationError(f"{field_name} must not contain duplicates.")
