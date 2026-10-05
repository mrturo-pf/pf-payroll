"""SQLAlchemy employer and employment-contract maintenance operations."""

import calendar
from datetime import date

from sqlalchemy import delete, select, true

from payroll.application.dto import (
    ContractMaintenanceResultDTO,
    EmployerMaintenanceDTO,
    EmployerMaintenanceResultDTO,
    EmployerReadDTO,
    FirstIncreasePeriodReadDTO,
    IncreaseReadDTO,
    PaymentDateReadDTO,
    EmployerReferenceDTO,
    EmploymentContractMaintenanceDTO,
    EmploymentContractNestedReadDTO,
    EmploymentContractReadDTO,
)
from payroll.application.errors import (
    PayrollConflictError,
    PayrollNotFoundError,
    PayrollValidationError,
)
from payroll.infrastructure.db.models import EmployerModel
from payroll.infrastructure.db.models.payroll import (
    EmploymentContractModel,
    EmployerFixedDayRoll,
    EmployerPaymentDateRule,
    PayrollPeriodModel,
)
from payroll.infrastructure.db.repositories.payroll_repository_shared import (
    SqlAlchemyPayrollRepositoryBase,
)


class SqlAlchemyEmployerContractRepositoryMixin(SqlAlchemyPayrollRepositoryBase):
    """Persistence operations for employer and contract maintenance."""

    async def maintain_employers(
        self, employers: list[EmployerMaintenanceDTO]
    ) -> EmployerMaintenanceResultDTO:
        """Create/update employers as one transaction-scoped batch."""
        models: list[EmployerModel] = []
        for command in employers:
            model: EmployerModel
            if command.id is None:
                model = EmployerModel(name=command.name)
                self._session.add(model)
                await self._session.flush()
            else:
                model = await self._load_employer(command.id, lock=True)
            model.name = command.name
            model.tax_id = command.tax_id
            model.country_code = command.country_code
            if command.payment_date_rule is not None:
                model.payment_date_rule = EmployerPaymentDateRule(
                    command.payment_date_rule
                )
            model.payment_month_offset = command.payment_month_offset
            model.payment_day_of_month = command.payment_day_of_month
            model.payment_business_day_offset = command.payment_business_day_offset
            model.payment_calendar_day_offset = command.payment_calendar_day_offset
            model.payment_effective_on_processing_next_day = (
                command.payment_effective_on_processing_next_day
            )
            if command.payment_fixed_day_roll is not None:
                model.payment_fixed_day_roll = EmployerFixedDayRoll(
                    command.payment_fixed_day_roll
                )
            models.append(model)
        return EmployerMaintenanceResultDTO(
            employers=[_employer_dto(model) for model in models]
        )

    async def maintain_contracts(
        self, contracts: list[EmploymentContractMaintenanceDTO]
    ) -> ContractMaintenanceResultDTO:
        """Create/update contracts as one transaction-scoped batch."""
        models: list[EmploymentContractModel] = []
        for command in contracts:
            _validate_contract_dates(command)
            await self._load_employer(command.employer_id, lock=True)
            await self._reject_overlapping_contract(command)
            if command.id is None:
                model = EmploymentContractModel(
                    employer_id=command.employer_id,
                    started_at=command.started_at,
                    ended_at=command.ended_at,
                    is_indefinite=command.is_indefinite,
                    position=command.position,
                )
                self._session.add(model)
                await self._session.flush()
            else:
                result = await self._session.execute(
                    select(EmploymentContractModel)
                    .where(EmploymentContractModel.id == command.id)
                    .with_for_update()
                )
                loaded_model = result.scalar_one_or_none()
                if loaded_model is None:
                    raise PayrollNotFoundError(
                        f"Employment contract {command.id} does not exist."
                    )
                model = loaded_model
                model.employer_id = command.employer_id
                model.started_at = command.started_at
                model.ended_at = command.ended_at
                model.is_indefinite = command.is_indefinite
                model.position = command.position
            models.append(model)
        return ContractMaintenanceResultDTO(
            contracts=[_contract_maintenance_dto(model) for model in models]
        )

    async def list_employers(self) -> list[EmployerReadDTO]:
        """List employers with their nested contracts ordered by ID."""
        result = await self._session.execute(
            select(EmployerModel).order_by(EmployerModel.id)
        )
        employers = list(result.scalars().all())
        contracts = await self._session.execute(
            select(EmploymentContractModel).order_by(
                EmploymentContractModel.employer_id,
                EmploymentContractModel.started_at,
                EmploymentContractModel.id,
            )
        )
        by_employer: dict[int, list[EmploymentContractNestedReadDTO]] = {}
        for model in contracts.scalars().all():
            by_employer.setdefault(model.employer_id, []).append(
                _contract_nested_dto(model)
            )
        return [
            EmployerReadDTO(
                id=model.id,
                name=model.name,
                tax_id=model.tax_id,
                country_code=model.country_code,
                increase=IncreaseReadDTO(
                    frequency=model.increase_frequency,
                    first_increase_period=(
                        FirstIncreasePeriodReadDTO(
                            model.first_increase_period_year,
                            model.first_increase_period_month,
                        )
                        if model.first_increase_period_year is not None
                        and model.first_increase_period_month is not None
                        else None
                    ),
                ),
                payment_date=PaymentDateReadDTO(
                    rule=model.payment_date_rule.value,
                    month_offset=model.payment_month_offset,
                    day_of_month=model.payment_day_of_month,
                    business_day_offset=model.payment_business_day_offset,
                    calendar_day_offset=model.payment_calendar_day_offset,
                    effective_on_processing_next_day=(
                        model.payment_effective_on_processing_next_day
                    ),
                    fixed_day_roll=model.payment_fixed_day_roll.value,
                ),
                contracts=by_employer.get(model.id, []),
            )
            for model in employers
        ]

    async def list_contracts(
        self, employer_id: int | None = None
    ) -> list[EmploymentContractReadDTO]:
        """List contracts with current-effect status."""
        statement = (
            select(
                EmploymentContractModel,
                EmployerModel.id,
                EmployerModel.name,
            )
            .join(
                EmployerModel, EmploymentContractModel.employer_id == EmployerModel.id
            )
            .order_by(
                EmploymentContractModel.employer_id,
                EmploymentContractModel.started_at,
                EmploymentContractModel.id,
            )
        )
        if employer_id is not None:
            statement = statement.where(
                EmploymentContractModel.employer_id == employer_id
            )
        result = await self._session.execute(statement)
        return [
            _contract_read_dto(model, employer_id, employer_name)
            for model, employer_id, employer_name in result.all()
        ]

    async def delete_employers(self, employer_ids: list[int]) -> None:
        """Delete employers only when contracts and payrolls are absent."""
        result = await self._session.execute(
            select(EmployerModel)
            .where(EmployerModel.id.in_(employer_ids))
            .with_for_update()
        )
        employers = list(result.scalars().all())
        found = {model.id for model in employers}
        missing = sorted(set(employer_ids) - found)
        if missing:
            raise PayrollNotFoundError(f"Employers do not exist: {missing}.")
        blockers: list[dict[str, object]] = []
        for employer_id in employer_ids:
            contracts = await self._session.execute(
                select(EmploymentContractModel.id).where(
                    EmploymentContractModel.employer_id == employer_id
                )
            )
            periods = await self._session.execute(
                select(PayrollPeriodModel.id).where(
                    PayrollPeriodModel.employer_id == employer_id
                )
            )
            contract_ids = list(contracts.scalars().all())
            period_ids = list(periods.scalars().all())
            if contract_ids or period_ids:
                blockers.append(
                    {
                        "employer_id": employer_id,
                        "contract_ids": contract_ids,
                        "period_ids": period_ids,
                    }
                )
        if blockers:
            raise PayrollConflictError(
                "One or more employers have dependent data.",
                detail={
                    "message": "One or more employers have dependent data.",
                    "employer_deletion_conflicts": blockers,
                },
            )
        await self._session.execute(
            delete(EmployerModel).where(EmployerModel.id.in_(employer_ids))
        )

    async def delete_contracts(self, contract_ids: list[int]) -> None:
        """Delete contracts that have no payroll in their worked-month range."""
        result = await self._session.execute(
            select(EmploymentContractModel)
            .where(EmploymentContractModel.id.in_(contract_ids))
            .with_for_update()
        )
        contracts = list(result.scalars().all())
        found = {model.id for model in contracts}
        missing = sorted(set(contract_ids) - found)
        if missing:
            raise PayrollNotFoundError(f"Employment contracts do not exist: {missing}.")
        blockers: list[dict[str, object]] = []
        for contract in contracts:
            periods_result = await self._session.execute(
                select(PayrollPeriodModel).where(
                    PayrollPeriodModel.employer_id == contract.employer_id
                )
            )
            period_ids = [
                period.id
                for period in periods_result.scalars().all()
                if _period_overlaps_contract(period, contract)
            ]
            if period_ids:
                blockers.append({"contract_id": contract.id, "period_ids": period_ids})
        if blockers:
            raise PayrollConflictError(
                "One or more contracts have payroll periods in their worked range.",
                detail={
                    "message": (
                        "One or more contracts have payroll periods in their worked "
                        "range."
                    ),
                    "contract_deletion_conflicts": blockers,
                },
            )
        await self._session.execute(
            delete(EmploymentContractModel).where(
                EmploymentContractModel.id.in_(contract_ids)
            )
        )

    async def _load_employer(self, employer_id: int, *, lock: bool) -> EmployerModel:
        """Load an employer or raise a stable not-found error."""
        statement = select(EmployerModel).where(EmployerModel.id == employer_id)
        if lock:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise PayrollNotFoundError(f"Employer {employer_id} does not exist.")
        return model

    async def _reject_overlapping_contract(
        self, command: EmploymentContractMaintenanceDTO
    ) -> None:
        """Reject any interval overlap, excluding the update target itself."""
        result = await self._session.execute(
            select(EmploymentContractModel).where(
                EmploymentContractModel.started_at <= (command.ended_at or date.max),
                (
                    EmploymentContractModel.ended_at.is_(None)
                    | (EmploymentContractModel.ended_at >= command.started_at)
                ),
                (
                    EmploymentContractModel.id != command.id
                    if command.id is not None
                    else true()
                ),
            )
        )
        conflicts = list(result.scalars().all())
        if conflicts:
            raise PayrollConflictError(
                "The employment contract interval overlaps an existing contract.",
                detail={
                    "message": (
                        "The employment contract interval overlaps an existing "
                        "contract."
                    ),
                    "contract_overlap_conflicts": [
                        {
                            "contract_id": conflict.id,
                            "employer_id": conflict.employer_id,
                        }
                        for conflict in conflicts
                    ],
                },
            )


def _validate_contract_dates(command: EmploymentContractMaintenanceDTO) -> None:
    """Validate contract interval semantics."""
    if command.is_indefinite and command.ended_at is not None:
        raise PayrollValidationError("An indefinite contract must not have ended_at.")
    if not command.is_indefinite and command.ended_at is None:
        raise PayrollValidationError("A finite contract must have ended_at.")
    if command.ended_at is not None and command.ended_at < command.started_at:
        raise PayrollValidationError("ended_at must not precede started_at.")


def _employer_dto(model: EmployerModel) -> EmployerMaintenanceDTO:
    """Convert an employer model to a maintenance DTO."""
    return EmployerMaintenanceDTO(
        id=model.id,
        name=model.name,
        tax_id=model.tax_id,
        country_code=model.country_code,
        payment_date_rule=model.payment_date_rule.value,
        payment_month_offset=model.payment_month_offset,
        payment_day_of_month=model.payment_day_of_month,
        payment_business_day_offset=model.payment_business_day_offset,
        payment_calendar_day_offset=model.payment_calendar_day_offset,
        payment_effective_on_processing_next_day=model.payment_effective_on_processing_next_day,
        payment_fixed_day_roll=model.payment_fixed_day_roll.value,
    )


def _contract_maintenance_dto(
    model: EmploymentContractModel,
) -> EmploymentContractMaintenanceDTO:
    """Convert a contract model to a mutation DTO."""
    return EmploymentContractMaintenanceDTO(
        id=model.id,
        employer_id=model.employer_id,
        started_at=model.started_at,
        ended_at=model.ended_at,
        is_indefinite=model.is_indefinite,
        position=model.position,
    )


def _contract_read_dto(
    model: EmploymentContractModel,
    employer_id: int,
    employer_name: str,
) -> EmploymentContractReadDTO:
    """Convert a contract model to its public read DTO."""
    return EmploymentContractReadDTO(
        id=model.id,
        employer=EmployerReferenceDTO(employer_id, employer_name),
        started_at=model.started_at,
        ended_at=model.ended_at,
        is_indefinite=model.is_indefinite,
        position=model.position,
        is_in_effect=_is_contract_in_effect(model),
    )


def _contract_nested_dto(
    model: EmploymentContractModel,
) -> EmploymentContractNestedReadDTO:
    """Convert a contract model to the employer-nested read shape."""
    return EmploymentContractNestedReadDTO(
        id=model.id,
        started_at=model.started_at,
        ended_at=model.ended_at,
        is_indefinite=model.is_indefinite,
        position=model.position,
        is_in_effect=_is_contract_in_effect(model),
    )


def _is_contract_in_effect(model: EmploymentContractModel) -> bool:
    """Return whether a contract is effective today."""
    today = date.today()
    return model.started_at <= today and (
        model.ended_at is None or today <= model.ended_at
    )


def _period_overlaps_contract(
    period: PayrollPeriodModel, contract: EmploymentContractModel
) -> bool:
    """Return whether a payroll worked month overlaps a contract interval."""
    month_start = date(period.period_year, period.period_month, 1)
    month_end = date(
        period.period_year,
        period.period_month,
        calendar.monthrange(period.period_year, period.period_month)[1],
    )
    return contract.started_at <= month_end and (
        contract.ended_at is None or contract.ended_at >= month_start
    )
