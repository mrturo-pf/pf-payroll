"""SQLAlchemy repository for reference data."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.dto import (
    ContributionCapDTO,
    CurrencyDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    IncomeTaxBracketDTO,
    IncomeTaxBracketWriteDTO,
    PayrollConceptDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
)
from payroll.infrastructure.db.models.reference_data import (
    ContributionCapModel,
    CurrencyModel,
    HealthInstitutionModel,
    HealthPlanModel,
    IncomeTaxBracketModel,
    PayrollConceptModel,
    PensionInstitutionModel,
    PensionPlanModel,
)


class SqlAlchemyReferenceDataRepository:
    """Provide sql alchemy reference data repository."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the instance."""
        self._session = session

    async def list_currencies(self) -> list[CurrencyDTO]:
        """List currencies."""
        result = await self._session.execute(
            select(CurrencyModel).order_by(CurrencyModel.code)
        )
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

    async def list_income_tax_brackets(self) -> list[IncomeTaxBracketDTO]:
        """List income tax brackets."""
        result = await self._session.execute(
            select(IncomeTaxBracketModel).order_by(
                IncomeTaxBracketModel.valid_from.desc(),
                IncomeTaxBracketModel.lower_bound_utm,
            )
        )
        return [
            IncomeTaxBracketDTO(
                valid_from=row.valid_from,
                valid_to=row.valid_to,
                lower_bound_utm=row.lower_bound_utm,
                upper_bound_utm=row.upper_bound_utm,
                marginal_rate=row.marginal_rate,
                rebate_utm=row.rebate_utm,
            )
            for row in result.scalars().all()
        ]

    async def upsert_income_tax_brackets(
        self, brackets: list[IncomeTaxBracketWriteDTO]
    ) -> int:
        """Handle upsert income tax brackets."""
        if not brackets:
            return 0

        statement = insert(IncomeTaxBracketModel).values(
            [
                {
                    "valid_from": item.valid_from,
                    "valid_to": item.valid_to,
                    "lower_bound_utm": item.lower_bound_utm,
                    "upper_bound_utm": item.upper_bound_utm,
                    "marginal_rate": item.marginal_rate,
                    "rebate_utm": item.rebate_utm,
                }
                for item in brackets
            ]
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    IncomeTaxBracketModel.valid_from,
                    IncomeTaxBracketModel.lower_bound_utm,
                ],
                set_={
                    "valid_to": statement.excluded.valid_to,
                    "upper_bound_utm": statement.excluded.upper_bound_utm,
                    "marginal_rate": statement.excluded.marginal_rate,
                    "rebate_utm": statement.excluded.rebate_utm,
                },
            )
        )
        await self._session.commit()
        return len(brackets)
