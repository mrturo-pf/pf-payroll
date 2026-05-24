"""Command-oriented payroll repository operations."""

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, or_, select

from payroll.application.dto import (
    AssignPlansCommandDTO,
    AssignPlansResultDTO,
    ComputeContributionsCommandDTO,
    ComputeContributionsResultDTO,
    ComputeIncomeTaxCommandDTO,
    ComputeIncomeTaxResultDTO,
    ComputeUnemploymentInsuranceCommandDTO,
    ComputeUnemploymentInsuranceResultDTO,
    ContributionComputationContextDTO,
    IncomeTaxContextDTO,
    ReviewPayrollPeriodCommandDTO,
    ReviewPayrollPeriodResultDTO,
    UnemploymentComputationContextDTO,
)
from payroll.application.errors import PayrollConflictError, PayrollNotFoundError
from payroll.domain.contributions import (
    ContributionCap,
    HealthInstitution,
    HealthPlan,
    PensionInstitution,
    PensionPlan,
)
from payroll.domain.taxes import IncomeTaxBracket
from payroll.infrastructure.db.models import (
    PayrollConceptModel,
    IncomeTaxBracketModel,
)
from payroll.infrastructure.db.models.payroll import PayrollItemModel, PayrollStatus
from payroll.infrastructure.db.models.reference_data import (
    ContributionCapType,
    PayrollConceptKind,
)
from payroll.infrastructure.db.repositories.payroll_repository_shared import (
    SqlAlchemyPayrollRepositoryBase,
)
from payroll.shared.constants import (
    COMPUTED_CONTRIBUTION_CONCEPT_CODES,
    INCOME_TAX_DEDUCTIBLE_CONCEPT_CODES,
    INCOME_TAX_CONCEPT_CODE,
    REVIEW_REQUIRED_CONCEPT_CODES,
)


class SqlAlchemyPayrollCommandRepository(SqlAlchemyPayrollRepositoryBase):
    """Write and calculation-context payroll operations."""

    async def assign_plans(
        self, command: AssignPlansCommandDTO
    ) -> AssignPlansResultDTO:
        """Assign plans."""
        period = await self._get_period(command.period_id)
        await self._get_pension_plan(command.pension_plan_id, period.payment_date)
        await self._get_health_plan(
            command.health_plan_id,
            period.payment_date,
            require_active=True,
        )

        period.pension_plan_id = command.pension_plan_id
        period.health_plan_id = command.health_plan_id

        await self._session.commit()

        return AssignPlansResultDTO(
            period_id=period.id,
            payment_date=period.payment_date,
            pension_plan_id=period.pension_plan_id,
            health_plan_id=period.health_plan_id,
        )

    async def review_period(
        self, command: ReviewPayrollPeriodCommandDTO
    ) -> ReviewPayrollPeriodResultDTO:
        """Review period."""
        period = await self._get_period(command.period_id)
        if period.pension_plan_id is None or period.health_plan_id is None:
            raise PayrollConflictError(
                f"Payroll period {period.id} must have pension "
                "and health plans assigned before review."
            )

        present_result = await self._session.execute(
            select(PayrollConceptModel.code)
            .join(
                PayrollItemModel, PayrollItemModel.concept_id == PayrollConceptModel.id
            )
            .where(PayrollItemModel.period_id == period.id)
            .where(PayrollConceptModel.code.in_(REVIEW_REQUIRED_CONCEPT_CODES))
        )
        present_codes = set(present_result.scalars().all())
        missing_codes = sorted(REVIEW_REQUIRED_CONCEPT_CODES - present_codes)
        if missing_codes:
            raise PayrollConflictError(
                "Payroll period "
                f"{period.id} must have computed contributions and income tax "
                "before review. Missing: "
                f"{', '.join(missing_codes)}"
            )

        period.status = PayrollStatus.REVIEWED
        await self._session.commit()
        return ReviewPayrollPeriodResultDTO(
            period_id=period.id,
            payment_date=period.payment_date,
            status=period.status.value,
        )

    async def get_contribution_context(
        self,
        command: ComputeContributionsCommandDTO,
    ) -> ContributionComputationContextDTO:
        """Get contribution context."""
        period = await self._get_period(command.period_id)
        pension_plan_model, pension_institution_model = await self._get_pension_plan(
            command.pension_plan_id,
            period.payment_date,
        )
        health_plan_model, health_institution_model = await self._get_health_plan(
            command.health_plan_id,
            period.payment_date,
        )

        cap_model = await self._get_latest_contribution_cap(
            cap_type=ContributionCapType.PENSION_HEALTH,
            payment_date=period.payment_date,
            missing_message=(
                f"No contribution cap was found for {period.payment_date.isoformat()}."
            ),
        )
        unemployment_cap_model = await self._get_latest_contribution_cap(
            cap_type=ContributionCapType.UNEMPLOYMENT,
            payment_date=period.payment_date,
            missing_message=(
                "No unemployment contribution cap was found for "
                f"{period.payment_date.isoformat()}."
            ),
        )

        taxable_result = await self._session.execute(
            select(func.coalesce(func.sum(PayrollItemModel.amount_clp), 0))
            .join(
                PayrollConceptModel,
                PayrollItemModel.concept_id == PayrollConceptModel.id,
            )
            .where(PayrollItemModel.period_id == period.id)
            .where(PayrollConceptModel.kind == PayrollConceptKind.INCOME)
            .where(PayrollConceptModel.is_taxable.is_(True))
        )
        taxable_income_clp = Decimal(taxable_result.scalar_one())

        return ContributionComputationContextDTO(
            period_id=period.id,
            payment_date=period.payment_date,
            taxable_income_clp=taxable_income_clp,
            employment_contract_kind=period.employment_contract_kind,
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
            unemployment_cap=ContributionCap(
                cap_type=unemployment_cap_model.cap_type.value,
                valid_from=unemployment_cap_model.valid_from,
                valid_to=unemployment_cap_model.valid_to,
                value_uf=unemployment_cap_model.value_uf,
            ),
        )

    async def save_computed_contributions(
        self,
        result: ComputeContributionsResultDTO,
    ) -> ComputeContributionsResultDTO:
        """Save computed contributions."""
        period = await self._get_period(result.period_id)

        concept_result = await self._session.execute(
            select(PayrollConceptModel).where(
                PayrollConceptModel.code.in_(COMPUTED_CONTRIBUTION_CONCEPT_CODES)
            )
        )
        concepts = {concept.code: concept for concept in concept_result.scalars().all()}
        missing_codes = sorted(COMPUTED_CONTRIBUTION_CONCEPT_CODES - set(concepts))
        if missing_codes:
            raise PayrollNotFoundError(
                "Missing payroll concepts for computed contributions: "
                f"{', '.join(missing_codes)}"
            )

        period.pension_plan_id = result.pension_plan_id
        period.health_plan_id = result.health_plan_id

        await self._session.execute(
            delete(PayrollItemModel).where(
                PayrollItemModel.period_id == result.period_id,
                PayrollItemModel.concept_id.in_(
                    [concept.id for concept in concepts.values()]
                ),
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
                PayrollItemModel(
                    period_id=result.period_id,
                    concept_id=concepts["UNEMPLOYMENT_INSURANCE"].id,
                    amount_clp=result.unemployment.employee_amount_clp,
                ),
            ]
        )

        await self._reconcile_period_net_pay(period, refresh_summary_view=True)
        return result

    async def get_unemployment_context(
        self, command: ComputeUnemploymentInsuranceCommandDTO
    ) -> UnemploymentComputationContextDTO:
        """Get unemployment computation context."""
        period = await self._get_period(command.period_id)
        unemployment_cap_model = await self._get_latest_contribution_cap(
            cap_type=ContributionCapType.UNEMPLOYMENT,
            payment_date=period.payment_date,
            missing_message=(
                "No unemployment contribution cap was found for "
                f"{period.payment_date.isoformat()}."
            ),
        )
        taxable_result = await self._session.execute(
            select(func.coalesce(func.sum(PayrollItemModel.amount_clp), 0))
            .join(
                PayrollConceptModel,
                PayrollItemModel.concept_id == PayrollConceptModel.id,
            )
            .where(PayrollItemModel.period_id == period.id)
            .where(PayrollConceptModel.kind == PayrollConceptKind.INCOME)
            .where(PayrollConceptModel.is_taxable.is_(True))
        )

        return UnemploymentComputationContextDTO(
            period_id=period.id,
            payment_date=period.payment_date,
            taxable_income_clp=Decimal(taxable_result.scalar_one()),
            employment_contract_kind=period.employment_contract_kind,
            unemployment_cap=ContributionCap(
                cap_type=unemployment_cap_model.cap_type.value,
                valid_from=unemployment_cap_model.valid_from,
                valid_to=unemployment_cap_model.valid_to,
                value_uf=unemployment_cap_model.value_uf,
            ),
        )

    async def save_computed_unemployment(
        self,
        result: ComputeUnemploymentInsuranceResultDTO,
    ) -> ComputeUnemploymentInsuranceResultDTO:
        """Save computed unemployment insurance."""
        period = await self._get_period(result.period_id)
        concept_result = await self._session.execute(
            select(PayrollConceptModel).where(
                PayrollConceptModel.code == "UNEMPLOYMENT_INSURANCE"
            )
        )
        concept = concept_result.scalar_one_or_none()
        if concept is None:
            raise PayrollNotFoundError(
                "Missing payroll concept for computed contributions: "
                "UNEMPLOYMENT_INSURANCE"
            )

        await self._session.execute(
            delete(PayrollItemModel).where(
                PayrollItemModel.period_id == result.period_id,
                PayrollItemModel.concept_id == concept.id,
            )
        )
        self._session.add(
            PayrollItemModel(
                period_id=result.period_id,
                concept_id=concept.id,
                amount_clp=result.unemployment.employee_amount_clp,
            )
        )

        await self._reconcile_period_net_pay(period, refresh_summary_view=True)
        return result

    async def get_income_tax_context(
        self, command: ComputeIncomeTaxCommandDTO
    ) -> IncomeTaxContextDTO:
        """Get income tax context."""
        period = await self._get_period(command.period_id)

        taxable_income_result = await self._session.execute(
            select(func.coalesce(func.sum(PayrollItemModel.amount_clp), 0))
            .join(
                PayrollConceptModel,
                PayrollItemModel.concept_id == PayrollConceptModel.id,
            )
            .where(PayrollItemModel.period_id == period.id)
            .where(PayrollConceptModel.kind == PayrollConceptKind.INCOME)
            .where(PayrollConceptModel.is_taxable.is_(True))
        )
        deductible_result = await self._session.execute(
            select(func.coalesce(func.sum(PayrollItemModel.amount_clp), 0))
            .join(
                PayrollConceptModel,
                PayrollItemModel.concept_id == PayrollConceptModel.id,
            )
            .where(PayrollItemModel.period_id == period.id)
            .where(PayrollConceptModel.code.in_(INCOME_TAX_DEDUCTIBLE_CONCEPT_CODES))
        )

        return IncomeTaxContextDTO(
            period_id=period.id,
            payment_date=period.payment_date,
            taxable_income_clp=Decimal(taxable_income_result.scalar_one()),
            deductible_amount_clp=Decimal(deductible_result.scalar_one()),
        )

    async def get_income_tax_bracket(
        self, payment_date: date, taxable_base_utm: Decimal
    ) -> IncomeTaxBracket | None:
        """Get income tax bracket."""
        result = await self._session.execute(
            select(IncomeTaxBracketModel)
            .where(IncomeTaxBracketModel.valid_from <= payment_date)
            .where(
                or_(
                    IncomeTaxBracketModel.valid_to.is_(None),
                    IncomeTaxBracketModel.valid_to >= payment_date,
                )
            )
            .where(IncomeTaxBracketModel.lower_bound_utm <= taxable_base_utm)
            .where(
                or_(
                    IncomeTaxBracketModel.upper_bound_utm.is_(None),
                    IncomeTaxBracketModel.upper_bound_utm > taxable_base_utm,
                )
            )
            .order_by(
                IncomeTaxBracketModel.valid_from.desc(),
                IncomeTaxBracketModel.lower_bound_utm.desc(),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None

        return IncomeTaxBracket(
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            lower_bound_utm=row.lower_bound_utm,
            upper_bound_utm=row.upper_bound_utm,
            marginal_rate=row.marginal_rate,
            rebate_utm=row.rebate_utm,
        )

    async def save_computed_income_tax(
        self,
        result: ComputeIncomeTaxResultDTO,
    ) -> ComputeIncomeTaxResultDTO:
        """Save computed income tax."""
        period = await self._get_period(result.period_id)

        concept_result = await self._session.execute(
            select(PayrollConceptModel).where(
                PayrollConceptModel.code == INCOME_TAX_CONCEPT_CODE
            )
        )
        concept = concept_result.scalar_one_or_none()
        if concept is None:
            raise PayrollNotFoundError(
                "Missing payroll concept for computed income tax: "
                f"{INCOME_TAX_CONCEPT_CODE}"
            )

        await self._session.execute(
            delete(PayrollItemModel).where(
                PayrollItemModel.period_id == result.period_id,
                PayrollItemModel.concept_id == concept.id,
            )
        )
        self._session.add(
            PayrollItemModel(
                period_id=result.period_id,
                concept_id=concept.id,
                amount_clp=result.tax.tax_clp,
            )
        )

        await self._reconcile_period_net_pay(period, refresh_summary_view=True)
        return result
