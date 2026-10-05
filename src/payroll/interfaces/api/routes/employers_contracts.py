"""Employer and employment-contract maintenance routes."""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from payroll.application.dto import (
    EmployerMaintenanceDTO,
    EmploymentContractMaintenanceDTO,
)
from payroll.application.errors import PayrollError
from payroll.application.use_cases.maintain_employers_contracts import (
    DeleteEmployers,
    DeleteEmploymentContracts,
    MaintainEmployers,
    MaintainEmploymentContracts,
)
from payroll.interfaces.api.dependencies import (
    get_payroll_repository,
    get_transactional_delete_contracts_use_case,
    get_transactional_delete_employers_use_case,
    get_transactional_maintain_contracts_use_case,
    get_transactional_maintain_employers_use_case,
    get_transactional_session,
)
from payroll.interfaces.api.errors import to_http_exception
from payroll.interfaces.session import TransactionalSessionScope
from payroll.application.ports.repositories import PayrollRepository

router = APIRouter(prefix="/payroll", tags=["payroll-maintenance"])


class EmployerMaintenanceRequest(BaseModel):
    """Represent an employer create/update batch."""

    employers: list["EmployerMaintenanceItem"] = Field(min_length=1)


class EmployerMaintenanceItem(BaseModel):
    """Represent one employer mutation."""

    id: int | None = None
    name: str = Field(min_length=1, max_length=120)
    tax_id: str | None = None
    country_code: str = Field(min_length=2, max_length=2)
    payment_date_rule: str | None = None
    payment_month_offset: int = 0
    payment_day_of_month: int | None = None
    payment_business_day_offset: int = 0
    payment_calendar_day_offset: int = 0
    payment_effective_on_processing_next_day: bool = False
    payment_fixed_day_roll: str | None = None


class ContractMaintenanceRequest(BaseModel):
    """Represent a contract create/update batch."""

    contracts: list["ContractMaintenanceItem"] = Field(min_length=1)


class ContractMaintenanceItem(BaseModel):
    """Represent one contract mutation."""

    id: int | None = None
    employer_id: int
    started_at: date
    ended_at: date | None = None
    is_indefinite: bool
    position: str | None = None


class IdBatchRequest(BaseModel):
    """Represent a deletion ID batch."""

    ids: list[int] = Field(min_length=1)


class EmployerMutationRead(EmployerMaintenanceItem):
    """Represent an employer mutation response."""

    id: int


class ContractMutationRead(ContractMaintenanceItem):
    """Represent a contract mutation response."""

    id: int


class EmployerReferenceRead(BaseModel):
    """Represent an employer nested in a contract response."""

    id: int
    name: str


class ContractRead(BaseModel):
    """Represent a public contract response."""

    id: int
    employer: EmployerReferenceRead
    started_at: date
    ended_at: date | None
    is_indefinite: bool
    position: str | None
    is_in_effect: bool


class NestedContractRead(BaseModel):
    """Represent a contract nested under an employer."""

    id: int
    started_at: date
    ended_at: date | None
    is_indefinite: bool
    position: str | None
    is_in_effect: bool


class FirstIncreasePeriodRead(BaseModel):
    """Represent the first salary-increase period."""

    year: int
    month: int


class PaymentDateRead(BaseModel):
    """Represent employer payment-date configuration."""

    rule: str | None
    month_offset: int
    day_of_month: int | None
    business_day_offset: int
    calendar_day_offset: int
    effective_on_processing_next_day: bool
    fixed_day_roll: str | None


class IncreaseRead(BaseModel):
    """Represent employer salary-increase configuration."""

    frequency: int | None
    first_increase_period: FirstIncreasePeriodRead | None


class EmployerRead(BaseModel):
    """Represent a public employer response with nested contracts."""

    id: int
    name: str
    tax_id: str | None
    country_code: str
    increase: IncreaseRead
    payment_date: PaymentDateRead
    contracts: list[NestedContractRead]


def _employer_command(item: EmployerMaintenanceItem) -> EmployerMaintenanceDTO:
    """Map an employer request to an application DTO."""
    return EmployerMaintenanceDTO(**item.model_dump())


def _contract_command(
    item: ContractMaintenanceItem,
) -> EmploymentContractMaintenanceDTO:
    """Map a contract request to an application DTO."""
    return EmploymentContractMaintenanceDTO(**item.model_dump())


@router.get("/employers", response_model=list[EmployerRead])
async def list_employers(
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> list[EmployerRead]:
    """List employers by stable ID."""
    return [EmployerRead(**asdict(item)) for item in await repository.list_employers()]


@router.post("/employers", response_model=list[EmployerMutationRead])
async def maintain_employers(
    payload: EmployerMaintenanceRequest,
    scope: TransactionalSessionScope = Depends(get_transactional_session),
    use_case: MaintainEmployers = Depends(
        get_transactional_maintain_employers_use_case
    ),
) -> list[EmployerMutationRead]:
    """Create/update an atomic employer batch."""
    try:
        result = await use_case.execute(
            [_employer_command(item) for item in payload.employers]
        )
    except PayrollError as exc:
        await scope.resolve("validate")
        raise to_http_exception(exc) from exc
    await scope.resolve("commit")
    return [EmployerMutationRead(**asdict(item)) for item in result.employers]


@router.delete("/employers", status_code=204)
async def delete_employers(
    payload: IdBatchRequest,
    scope: TransactionalSessionScope = Depends(get_transactional_session),
    use_case: DeleteEmployers = Depends(get_transactional_delete_employers_use_case),
) -> Response:
    """Delete an employer batch when no dependents exist."""
    try:
        await use_case.execute(payload.ids)
    except PayrollError as exc:
        await scope.resolve("validate")
        raise to_http_exception(exc) from exc
    await scope.resolve("commit")
    return Response(status_code=204)


@router.get("/contracts", response_model=list[ContractRead])
async def list_contracts(
    employer_id: int | None = Query(default=None, gt=0),
    repository: PayrollRepository = Depends(get_payroll_repository),
) -> list[ContractRead]:
    """List contracts, optionally filtered by employer ID."""
    return [
        ContractRead(**asdict(item))
        for item in await repository.list_contracts(employer_id)
    ]


@router.post("/contracts", response_model=list[ContractMutationRead])
async def maintain_contracts(
    payload: ContractMaintenanceRequest,
    scope: TransactionalSessionScope = Depends(get_transactional_session),
    use_case: MaintainEmploymentContracts = Depends(
        get_transactional_maintain_contracts_use_case
    ),
) -> list[ContractMutationRead]:
    """Create/update an atomic contract batch."""
    try:
        result = await use_case.execute(
            [_contract_command(item) for item in payload.contracts]
        )
    except PayrollError as exc:
        await scope.resolve("validate")
        raise to_http_exception(exc) from exc
    await scope.resolve("commit")
    return [ContractMutationRead(**asdict(item)) for item in result.contracts]


@router.delete("/contracts", status_code=204)
async def delete_contracts(
    payload: IdBatchRequest,
    scope: TransactionalSessionScope = Depends(get_transactional_session),
    use_case: DeleteEmploymentContracts = Depends(
        get_transactional_delete_contracts_use_case
    ),
) -> Response:
    """Delete a contract batch when no payroll blocks deletion."""
    try:
        await use_case.execute(payload.ids)
    except PayrollError as exc:
        await scope.resolve("validate")
        raise to_http_exception(exc) from exc
    await scope.resolve("commit")
    return Response(status_code=204)
