"""SQLAlchemy repository for reference data."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.dto import (
    ContributionCapDTO,
    CurrencyDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    PayrollConceptDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
)
from payroll.infrastructure.db.models.reference_data import (
    ContributionCapModel,
    CurrencyModel,
    HealthInstitutionModel,
    HealthPlanModel,
    PayrollConceptModel,
    PensionInstitutionModel,
    PensionPlanModel,
)


class SqlAlchemyReferenceDataRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_currencies(self) -> list[CurrencyDTO]:
        result = await self._session.execute(select(CurrencyModel).order_by(CurrencyModel.code))
        return [
            CurrencyDTO(
                code=row.code.strip(),
                name=row.name,
                is_fiat=row.is_fiat,
                unit_kind=row.unit_kind,
            )
            for row in result.scalars().all()
        ]

    async def list_pension_institutions(self) -> list[PensionInstitutionDTO]:
        result = await self._session.execute(
            select(PensionInstitutionModel).order_by(PensionInstitutionModel.name)
        )
        return [
            PensionInstitutionDTO(
                code=row.code,
                name=row.name,
                mandatory_rate=row.mandatory_rate,
                is_active=row.is_active,
            )
            for row in result.scalars().all()
        ]

    async def list_health_institutions(self) -> list[HealthInstitutionDTO]:
        result = await self._session.execute(
            select(HealthInstitutionModel).order_by(HealthInstitutionModel.name)
        )
        return [
            HealthInstitutionDTO(
                code=row.code,
                name=row.name,
                kind=row.kind,
                mandatory_rate=row.mandatory_rate,
                is_active=row.is_active,
            )
            for row in result.scalars().all()
        ]

    async def list_pension_plans(self) -> list[PensionPlanDTO]:
        result = await self._session.execute(
            select(PensionPlanModel, PensionInstitutionModel)
            .join(PensionInstitutionModel, PensionPlanModel.institution_id == PensionInstitutionModel.id)
            .order_by(PensionInstitutionModel.name, PensionPlanModel.valid_from)
        )
        return [
            PensionPlanDTO(
                id=plan.id,
                institution_code=institution.code,
                institution_name=institution.name,
                valid_from=plan.valid_from,
                valid_to=plan.valid_to,
                additional_rate=plan.additional_rate,
            )
            for plan, institution in result.all()
        ]

    async def list_health_plans(self) -> list[HealthPlanDTO]:
        result = await self._session.execute(
            select(HealthPlanModel, HealthInstitutionModel)
            .join(HealthInstitutionModel, HealthPlanModel.institution_id == HealthInstitutionModel.id)
            .order_by(HealthInstitutionModel.name, HealthPlanModel.valid_from)
        )
        return [
            HealthPlanDTO(
                id=plan.id,
                institution_code=institution.code,
                institution_name=institution.name,
                institution_kind=institution.kind,
                valid_from=plan.valid_from,
                valid_to=plan.valid_to,
                plan_name=plan.plan_name,
                contracted_uf=plan.contracted_uf,
            )
            for plan, institution in result.all()
        ]

    async def list_contribution_caps(self) -> list[ContributionCapDTO]:
        result = await self._session.execute(
            select(ContributionCapModel).order_by(ContributionCapModel.cap_type, ContributionCapModel.valid_from)
        )
        return [
            ContributionCapDTO(
                cap_type=row.cap_type.value,
                valid_from=row.valid_from,
                valid_to=row.valid_to,
                value_uf=row.value_uf,
            )
            for row in result.scalars().all()
        ]

    async def list_payroll_concepts(self) -> list[PayrollConceptDTO]:
        result = await self._session.execute(select(PayrollConceptModel).order_by(PayrollConceptModel.code))
        return [
            PayrollConceptDTO(
                code=row.code,
                name=row.name,
                kind=row.kind.value,
                is_taxable=row.is_taxable,
            )
            for row in result.scalars().all()
        ]
