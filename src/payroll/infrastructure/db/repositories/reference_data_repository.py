"""SQLAlchemy repository for reference data."""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import and_, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.dto import (
    ContributionCapDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    PayrollConceptDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
)
from payroll.infrastructure.db.models.reference_data import (
    ContributionCapModel,
    HealthInstitutionModel,
    HealthPlanModel,
    PayrollConceptModel,
    PensionInstitutionModel,
    PensionPlanModel,
)


def _to_health_plan_dtos(
    rows: Sequence[Row[tuple[HealthPlanModel, HealthInstitutionModel]]],
) -> list[HealthPlanDTO]:
    """Map (HealthPlanModel, HealthInstitutionModel) rows to HealthPlanDTOs."""
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
        for plan, institution in rows
    ]


class SqlAlchemyReferenceDataRepository:
    """Provide sql alchemy reference data repository."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the instance."""
        self._session = session

    async def list_pension_institutions(self) -> list[PensionInstitutionDTO]:
        """List pension institutions."""
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

    async def list_health_institutions(
        self, *, include_inactive: bool = False
    ) -> list[HealthInstitutionDTO]:
        """List health institutions."""
        statement = select(HealthInstitutionModel).order_by(HealthInstitutionModel.name)
        if not include_inactive:
            statement = statement.where(HealthInstitutionModel.is_active.is_(True))
        result = await self._session.execute(statement)
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
        """List pension plans."""
        result = await self._session.execute(
            select(PensionPlanModel, PensionInstitutionModel)
            .join(
                PensionInstitutionModel,
                PensionPlanModel.institution_id == PensionInstitutionModel.id,
            )
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

    async def list_health_plans(
        self, *, include_inactive: bool = False
    ) -> list[HealthPlanDTO]:
        """List health plans."""
        statement = (
            select(HealthPlanModel, HealthInstitutionModel)
            .join(
                HealthInstitutionModel,
                HealthPlanModel.institution_id == HealthInstitutionModel.id,
            )
            .order_by(HealthInstitutionModel.name, HealthPlanModel.valid_from)
        )
        if not include_inactive:
            statement = statement.where(HealthInstitutionModel.is_active.is_(True))
        result = await self._session.execute(statement)
        return _to_health_plan_dtos(result.all())

    async def list_contribution_caps(self) -> list[ContributionCapDTO]:
        """List contribution caps."""
        result = await self._session.execute(
            select(ContributionCapModel).order_by(
                ContributionCapModel.cap_type, ContributionCapModel.valid_from
            )
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
        """List payroll concepts."""
        result = await self._session.execute(
            select(PayrollConceptModel).order_by(PayrollConceptModel.code)
        )
        return [
            PayrollConceptDTO(
                code=row.code,
                name=row.name,
                kind=row.kind.value,
                is_taxable=row.is_taxable,
            )
            for row in result.scalars().all()
        ]

    async def get_valid_pension_plan_for_date(
        self, reference_date: date
    ) -> PensionPlanDTO | None:
        """Get the valid pension plan for a given reference date.

        A plan is valid if:
        - reference_date >= valid_from AND
        - (valid_to IS NULL OR reference_date <= valid_to)
        """
        result = await self._session.execute(
            select(PensionPlanModel, PensionInstitutionModel)
            .join(
                PensionInstitutionModel,
                PensionPlanModel.institution_id == PensionInstitutionModel.id,
            )
            .where(
                and_(
                    PensionPlanModel.valid_from <= reference_date,
                    or_(
                        PensionPlanModel.valid_to.is_(None),
                        PensionPlanModel.valid_to >= reference_date,
                    ),
                )
            )
            .order_by(PensionPlanModel.valid_from.desc())
        )
        rows = result.all()
        if not rows:
            return None
        plan, institution = rows[0]
        return PensionPlanDTO(
            id=plan.id,
            institution_code=institution.code,
            institution_name=institution.name,
            valid_from=plan.valid_from,
            valid_to=plan.valid_to,
            additional_rate=plan.additional_rate,
        )

    async def get_valid_health_plans_for_date(
        self, reference_date: date
    ) -> list[HealthPlanDTO]:
        """Get valid health plans for a given reference date.

        A plan is valid if:
        - reference_date >= valid_from AND
        - (valid_to IS NULL OR reference_date <= valid_to)
        """
        result = await self._session.execute(
            select(HealthPlanModel, HealthInstitutionModel)
            .join(
                HealthInstitutionModel,
                HealthPlanModel.institution_id == HealthInstitutionModel.id,
            )
            .where(
                and_(
                    HealthPlanModel.valid_from <= reference_date,
                    or_(
                        HealthPlanModel.valid_to.is_(None),
                        HealthPlanModel.valid_to >= reference_date,
                    ),
                )
            )
            .order_by(HealthPlanModel.valid_from.desc())
        )
        return _to_health_plan_dtos(result.all())
