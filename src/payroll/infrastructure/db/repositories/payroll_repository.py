"""SQLAlchemy repository for payroll persistence."""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.dto import (
    ComputeContributionsCommandDTO,
    ComputeContributionsResultDTO,
    ContributionComputationContextDTO,
    ImportPayrollResultDTO,
    ImportPayrollRowDTO,
    ImportedPayrollPeriodDTO,
)
from payroll.domain.contributions import (
    ContributionCap,
    HealthInstitution,
    HealthPlan,
    PensionInstitution,
    PensionPlan,
)
from payroll.infrastructure.db.models import (
    ContributionCapModel,
    EmployerModel,
    HealthInstitutionModel,
    HealthPlanModel,
    PayrollConceptModel,
    PensionInstitutionModel,
    PensionPlanModel,
)
from payroll.infrastructure.db.models.reference_data import ContributionCapType, PayrollConceptKind
from payroll.infrastructure.db.models.payroll import PayrollItemModel, PayrollPeriodModel, PayrollStatus


class SqlAlchemyPayrollRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def import_rows(self, rows: list[ImportPayrollRowDTO]) -> ImportPayrollResultDTO:
        if not rows:
            return ImportPayrollResultDTO(imported_periods=0, imported_items=0, periods=[])

        concept_result = await self._session.execute(
            select(PayrollConceptModel).where(PayrollConceptModel.code.in_({row.concept_code for row in rows}))
        )
        concepts = {concept.code: concept for concept in concept_result.scalars().all()}
        missing_codes = sorted({row.concept_code for row in rows} - set(concepts))
        if missing_codes:
            raise ValueError(f"Unknown payroll concepts in import: {', '.join(missing_codes)}")

        grouped_rows: dict[tuple[str, int, int], list[ImportPayrollRowDTO]] = defaultdict(list)
        for row in rows:
            grouped_rows[(row.employer, row.period_year, row.period_month)].append(row)

        imported_periods: list[ImportedPayrollPeriodDTO] = []
        imported_items = 0

        for (employer_name, year, month), period_rows in sorted(grouped_rows.items()):
            first_row = period_rows[0]

            employer_result = await self._session.execute(
                select(EmployerModel).where(EmployerModel.name == employer_name)
            )
            employer = employer_result.scalar_one_or_none()
            if employer is None:
                employer = EmployerModel(name=employer_name, started_at=first_row.payment_date)
                self._session.add(employer)
                await self._session.flush()

            period_result = await self._session.execute(
                select(PayrollPeriodModel).where(
                    PayrollPeriodModel.employer_id == employer.id,
                    PayrollPeriodModel.period_year == year,
                    PayrollPeriodModel.period_month == month,
                )
            )
            period = period_result.scalar_one_or_none()
            if period is None:
                period = PayrollPeriodModel(
                    employer_id=employer.id,
                    period_year=year,
                    period_month=month,
                    payment_date=first_row.payment_date,
                    status=PayrollStatus(first_row.status),
                )
                self._session.add(period)
                await self._session.flush()
            else:
                period.payment_date = first_row.payment_date
                period.status = PayrollStatus(first_row.status)
                await self._session.execute(delete(PayrollItemModel).where(PayrollItemModel.period_id == period.id))

            items = [
                PayrollItemModel(
                    period_id=period.id,
                    concept_id=concepts[row.concept_code].id,
                    amount_clp=row.amount_clp,
                )
                for row in period_rows
            ]
            self._session.add_all(items)
            imported_items += len(items)

            imported_periods.append(
                ImportedPayrollPeriodDTO(
                    id=period.id,
                    employer=employer.name,
                    period_year=year,
                    period_month=month,
                    payment_date=period.payment_date,
                    status=period.status.value,
                    item_count=len(items),
                )
            )

        await self._session.commit()
        await self._session.execute(text("REFRESH MATERIALIZED VIEW mv_payroll_summary"))
        await self._session.commit()

        return ImportPayrollResultDTO(
            imported_periods=len(imported_periods),
            imported_items=imported_items,
            periods=imported_periods,
        )

    async def get_contribution_context(
        self,
        command: ComputeContributionsCommandDTO,
    ) -> ContributionComputationContextDTO:
        period_result = await self._session.execute(
            select(PayrollPeriodModel).where(PayrollPeriodModel.id == command.period_id)
        )
        period = period_result.scalar_one_or_none()
        if period is None:
            raise ValueError(f"Payroll period {command.period_id} was not found.")

        pension_result = await self._session.execute(
            select(PensionPlanModel, PensionInstitutionModel)
            .join(PensionInstitutionModel, PensionPlanModel.institution_id == PensionInstitutionModel.id)
            .where(PensionPlanModel.id == command.pension_plan_id)
        )
        pension_row = pension_result.first()
        if pension_row is None:
            raise ValueError(f"Pension plan {command.pension_plan_id} was not found.")
        pension_plan_model, pension_institution_model = pension_row

        health_result = await self._session.execute(
            select(HealthPlanModel, HealthInstitutionModel)
            .join(HealthInstitutionModel, HealthPlanModel.institution_id == HealthInstitutionModel.id)
            .where(HealthPlanModel.id == command.health_plan_id)
        )
        health_row = health_result.first()
        if health_row is None:
            raise ValueError(f"Health plan {command.health_plan_id} was not found.")
        health_plan_model, health_institution_model = health_row

        cap_result = await self._session.execute(
            select(ContributionCapModel)
            .where(ContributionCapModel.cap_type == ContributionCapType.PENSION_HEALTH)
            .where(ContributionCapModel.valid_from <= period.payment_date)
            .where(
                or_(
                    ContributionCapModel.valid_to.is_(None),
                    ContributionCapModel.valid_to >= period.payment_date,
                )
            )
            .order_by(ContributionCapModel.valid_from.desc())
        )
        cap_model = cap_result.scalar_one_or_none()
        if cap_model is None:
            raise ValueError(f"No contribution cap was found for {period.payment_date.isoformat()}.")

        taxable_result = await self._session.execute(
            select(func.coalesce(func.sum(PayrollItemModel.amount_clp), 0))
            .join(PayrollConceptModel, PayrollItemModel.concept_id == PayrollConceptModel.id)
            .where(PayrollItemModel.period_id == period.id)
            .where(PayrollConceptModel.kind == PayrollConceptKind.INCOME)
            .where(PayrollConceptModel.is_taxable.is_(True))
        )
        taxable_income_clp = Decimal(taxable_result.scalar_one())

        return ContributionComputationContextDTO(
            period_id=period.id,
            payment_date=period.payment_date,
            taxable_income_clp=taxable_income_clp,
            pension_plan=PensionPlan(
                id=pension_plan_model.id,
                institution=PensionInstitution(
                    code=pension_institution_model.code,
                    name=pension_institution_model.name,
                    mandatory_rate=pension_institution_model.mandatory_rate,
                ),
                valid_from=pension_plan_model.valid_from,
                valid_to=pension_plan_model.valid_to,
                additional_rate=pension_plan_model.additional_rate,
            ),
            health_plan=HealthPlan(
                id=health_plan_model.id,
                institution=HealthInstitution(
                    code=health_institution_model.code,
                    name=health_institution_model.name,
                    kind=health_institution_model.kind,
                    mandatory_rate=health_institution_model.mandatory_rate,
                ),
                valid_from=health_plan_model.valid_from,
                valid_to=health_plan_model.valid_to,
                plan_name=health_plan_model.plan_name,
                contracted_uf=health_plan_model.contracted_uf,
            ),
            cap=ContributionCap(
                cap_type=cap_model.cap_type.value,
                valid_from=cap_model.valid_from,
                valid_to=cap_model.valid_to,
                value_uf=cap_model.value_uf,
            ),
        )

    async def save_computed_contributions(
        self,
        result: ComputeContributionsResultDTO,
    ) -> ComputeContributionsResultDTO:
        period_result = await self._session.execute(
            select(PayrollPeriodModel).where(PayrollPeriodModel.id == result.period_id)
        )
        period = period_result.scalar_one_or_none()
        if period is None:
            raise ValueError(f"Payroll period {result.period_id} was not found.")

        concept_codes = {
            "PENSION_BASE",
            "PENSION_ADDITIONAL",
            "HEALTH_BASE",
            "HEALTH_ADDITIONAL_UF",
        }
        concept_result = await self._session.execute(
            select(PayrollConceptModel).where(PayrollConceptModel.code.in_(concept_codes))
        )
        concepts = {concept.code: concept for concept in concept_result.scalars().all()}
        missing_codes = sorted(concept_codes - set(concepts))
        if missing_codes:
            raise ValueError(f"Missing payroll concepts for computed contributions: {', '.join(missing_codes)}")

        period.pension_plan_id = result.pension_plan_id
        period.health_plan_id = result.health_plan_id

        await self._session.execute(
            delete(PayrollItemModel).where(
                PayrollItemModel.period_id == result.period_id,
                PayrollItemModel.concept_id.in_([concept.id for concept in concepts.values()]),
            )
        )
        self._session.add_all(
            [
                PayrollItemModel(
                    period_id=result.period_id,
                    concept_id=concepts["PENSION_BASE"].id,
                    amount_clp=result.pension.base_amount_clp,
                ),
                PayrollItemModel(
                    period_id=result.period_id,
                    concept_id=concepts["PENSION_ADDITIONAL"].id,
                    amount_clp=result.pension.additional_amount_clp,
                ),
                PayrollItemModel(
                    period_id=result.period_id,
                    concept_id=concepts["HEALTH_BASE"].id,
                    amount_clp=result.health.base_amount_clp,
                ),
                PayrollItemModel(
                    period_id=result.period_id,
                    concept_id=concepts["HEALTH_ADDITIONAL_UF"].id,
                    amount_clp=result.health.additional_amount_clp,
                ),
            ]
        )

        await self._session.commit()
        await self._session.execute(text("REFRESH MATERIALIZED VIEW mv_payroll_summary"))
        await self._session.commit()
        return result
