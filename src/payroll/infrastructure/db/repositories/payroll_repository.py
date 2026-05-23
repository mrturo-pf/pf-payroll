"""SQLAlchemy repository for payroll persistence."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.dto import (
    AssignPlansCommandDTO,
    AssignPlansResultDTO,
    ComputeContributionsCommandDTO,
    ComputeContributionsResultDTO,
    ComputeIncomeTaxCommandDTO,
    ComputeIncomeTaxResultDTO,
    ContributionComputationContextDTO,
    IncomeTaxContextDTO,
    ImportPayrollResultDTO,
    ImportPayrollRowDTO,
    ImportedPayrollPeriodDTO,
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
    ReviewPayrollPeriodCommandDTO,
    ReviewPayrollPeriodResultDTO,
)
from payroll.domain.contributions import (
    ContributionCap,
    HealthInstitution,
    HealthPlan,
    PensionInstitution,
    PensionPlan,
)
from payroll.domain.taxes import IncomeTaxBracket
from payroll.infrastructure.db.models import (
    ContributionCapModel,
    EmployerModel,
    HealthInstitutionModel,
    HealthPlanModel,
    IncomeTaxBracketModel,
    PayrollConceptModel,
    PensionInstitutionModel,
    PensionPlanModel,
    PayrollSummaryModel,
)
from payroll.infrastructure.db.models.reference_data import ContributionCapType, PayrollConceptKind
from payroll.infrastructure.db.models.payroll import PayrollItemModel, PayrollPeriodModel, PayrollStatus


class SqlAlchemyPayrollRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get_period(self, period_id: int) -> PayrollPeriodModel:
        period_result = await self._session.execute(
            select(PayrollPeriodModel).where(PayrollPeriodModel.id == period_id)
        )
        period = period_result.scalar_one_or_none()
        if period is None:
            raise ValueError(f"Payroll period {period_id} was not found.")
        return period

    async def _get_pension_plan(
        self,
        plan_id: int,
        payment_date: date,
    ) -> tuple[PensionPlanModel, PensionInstitutionModel]:
        pension_result = await self._session.execute(
            select(PensionPlanModel, PensionInstitutionModel)
            .join(PensionInstitutionModel, PensionPlanModel.institution_id == PensionInstitutionModel.id)
            .where(PensionPlanModel.id == plan_id)
        )
        pension_row = pension_result.first()
        if pension_row is None:
            raise ValueError(f"Pension plan {plan_id} was not found.")

        pension_plan_model, pension_institution_model = pension_row
        if pension_plan_model.valid_from > payment_date or (
            pension_plan_model.valid_to is not None and pension_plan_model.valid_to < payment_date
        ):
            raise ValueError(f"Pension plan {plan_id} is not valid for {payment_date.isoformat()}.")

        return pension_plan_model, pension_institution_model

    async def _get_health_plan(
        self,
        plan_id: int,
        payment_date: date,
    ) -> tuple[HealthPlanModel, HealthInstitutionModel]:
        health_result = await self._session.execute(
            select(HealthPlanModel, HealthInstitutionModel)
            .join(HealthInstitutionModel, HealthPlanModel.institution_id == HealthInstitutionModel.id)
            .where(HealthPlanModel.id == plan_id)
        )
        health_row = health_result.first()
        if health_row is None:
            raise ValueError(f"Health plan {plan_id} was not found.")

        health_plan_model, health_institution_model = health_row
        if health_plan_model.valid_from > payment_date or (
            health_plan_model.valid_to is not None and health_plan_model.valid_to < payment_date
        ):
            raise ValueError(f"Health plan {plan_id} is not valid for {payment_date.isoformat()}.")

        return health_plan_model, health_institution_model

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
                    employment_contract_kind=first_row.employment_contract_kind,
                )
                self._session.add(period)
                await self._session.flush()
            else:
                period.payment_date = first_row.payment_date
                period.status = PayrollStatus(first_row.status)
                period.employment_contract_kind = first_row.employment_contract_kind
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
                    employment_contract_kind=period.employment_contract_kind,
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

    async def assign_plans(self, command: AssignPlansCommandDTO) -> AssignPlansResultDTO:
        period = await self._get_period(command.period_id)
        await self._get_pension_plan(command.pension_plan_id, period.payment_date)
        await self._get_health_plan(command.health_plan_id, period.payment_date)

        period.pension_plan_id = command.pension_plan_id
        period.health_plan_id = command.health_plan_id

        await self._session.commit()

        return AssignPlansResultDTO(
            period_id=period.id,
            payment_date=period.payment_date,
            pension_plan_id=period.pension_plan_id,
            health_plan_id=period.health_plan_id,
        )

    async def review_period(self, command: ReviewPayrollPeriodCommandDTO) -> ReviewPayrollPeriodResultDTO:
        period = await self._get_period(command.period_id)
        if period.pension_plan_id is None or period.health_plan_id is None:
            raise ValueError(
                f"Payroll period {period.id} must have pension and health plans assigned before review."
            )

        required_codes = {
            "PENSION_BASE",
            "PENSION_ADDITIONAL",
            "HEALTH_BASE",
            "HEALTH_ADDITIONAL_UF",
            "UNEMPLOYMENT_INSURANCE",
            "INCOME_TAX",
        }
        present_result = await self._session.execute(
            select(PayrollConceptModel.code)
            .join(PayrollItemModel, PayrollItemModel.concept_id == PayrollConceptModel.id)
            .where(PayrollItemModel.period_id == period.id)
            .where(PayrollConceptModel.code.in_(required_codes))
        )
        present_codes = set(present_result.scalars().all())
        missing_codes = sorted(required_codes - present_codes)
        if missing_codes:
            raise ValueError(
                "Payroll period "
                f"{period.id} must have computed contributions and income tax before review. Missing: "
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
        period = await self._get_period(command.period_id)
        pension_plan_model, pension_institution_model = await self._get_pension_plan(
            command.pension_plan_id,
            period.payment_date,
        )
        health_plan_model, health_institution_model = await self._get_health_plan(
            command.health_plan_id,
            period.payment_date,
        )

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

        unemployment_cap_result = await self._session.execute(
            select(ContributionCapModel)
            .where(ContributionCapModel.cap_type == ContributionCapType.UNEMPLOYMENT)
            .where(ContributionCapModel.valid_from <= period.payment_date)
            .where(
                or_(
                    ContributionCapModel.valid_to.is_(None),
                    ContributionCapModel.valid_to >= period.payment_date,
                )
            )
            .order_by(ContributionCapModel.valid_from.desc())
        )
        unemployment_cap_model = unemployment_cap_result.scalar_one_or_none()
        if unemployment_cap_model is None:
            raise ValueError(f"No unemployment contribution cap was found for {period.payment_date.isoformat()}.")

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
        period = await self._get_period(result.period_id)

        concept_codes = {
            "PENSION_BASE",
            "PENSION_ADDITIONAL",
            "HEALTH_BASE",
            "HEALTH_ADDITIONAL_UF",
            "UNEMPLOYMENT_INSURANCE",
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
                PayrollItemModel(
                    period_id=result.period_id,
                    concept_id=concepts["UNEMPLOYMENT_INSURANCE"].id,
                    amount_clp=result.unemployment.employee_amount_clp,
                ),
            ]
        )

        await self._session.commit()
        await self._session.execute(text("REFRESH MATERIALIZED VIEW mv_payroll_summary"))
        await self._session.commit()
        return result

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO | None:
        period_result = await self._session.execute(
            select(PayrollPeriodModel, EmployerModel)
            .join(EmployerModel, PayrollPeriodModel.employer_id == EmployerModel.id)
            .where(PayrollPeriodModel.id == period_id)
        )
        period_row = period_result.first()
        if period_row is None:
            return None
        period, employer = period_row

        items_result = await self._session.execute(
            select(PayrollItemModel, PayrollConceptModel)
            .join(PayrollConceptModel, PayrollItemModel.concept_id == PayrollConceptModel.id)
            .where(PayrollItemModel.period_id == period.id)
            .order_by(PayrollConceptModel.kind, PayrollConceptModel.code)
        )
        items = [
            PayrollItemDetailDTO(
                concept_code=concept.code,
                concept_name=concept.name,
                kind=concept.kind.value,
                is_taxable=concept.is_taxable,
                amount_clp=item.amount_clp,
                notes=item.notes,
            )
            for item, concept in items_result.all()
        ]

        summary_result = await self._session.execute(
            select(PayrollSummaryModel, EmployerModel)
            .join(EmployerModel, PayrollSummaryModel.employer_id == EmployerModel.id)
            .where(PayrollSummaryModel.period_id == period.id)
        )
        summary_row = summary_result.first()
        summary = None
        if summary_row is not None:
            summary_model, summary_employer = summary_row
            summary = PayrollSummaryDTO(
                period_id=summary_model.period_id,
                employer_id=summary_model.employer_id,
                employer_name=summary_employer.name,
                period_year=summary_model.period_year,
                period_month=summary_model.period_month,
                payment_date=summary_model.payment_date,
                taxable_income_clp=summary_model.taxable_income_clp,
                gross_income_clp=summary_model.gross_income_clp,
                total_discounts_clp=summary_model.total_discounts_clp,
                net_pay_clp=summary_model.net_pay_clp,
            )

        return PayrollPeriodDetailDTO(
            id=period.id,
            employer_id=employer.id,
            employer_name=employer.name,
            employer_tax_id=employer.tax_id,
            employer_country_code=employer.country_code,
            period_year=period.period_year,
            period_month=period.period_month,
            payment_date=period.payment_date,
            worked_days=period.worked_days,
            status=period.status.value,
            employment_contract_kind=period.employment_contract_kind,
            pension_plan_id=period.pension_plan_id,
            health_plan_id=period.health_plan_id,
            items=items,
            summary=summary,
        )

    async def list_period_summaries(self) -> list[PayrollSummaryDTO]:
        result = await self._session.execute(
            select(PayrollSummaryModel, EmployerModel)
            .join(EmployerModel, PayrollSummaryModel.employer_id == EmployerModel.id)
            .order_by(PayrollSummaryModel.period_year.desc(), PayrollSummaryModel.period_month.desc(), EmployerModel.name)
        )
        return [
            PayrollSummaryDTO(
                period_id=summary.period_id,
                employer_id=summary.employer_id,
                employer_name=employer.name,
                period_year=summary.period_year,
                period_month=summary.period_month,
                payment_date=summary.payment_date,
                taxable_income_clp=summary.taxable_income_clp,
                gross_income_clp=summary.gross_income_clp,
                total_discounts_clp=summary.total_discounts_clp,
                net_pay_clp=summary.net_pay_clp,
            )
            for summary, employer in result.all()
        ]

    async def get_income_tax_context(self, command: ComputeIncomeTaxCommandDTO) -> IncomeTaxContextDTO:
        period_result = await self._session.execute(
            select(PayrollPeriodModel).where(PayrollPeriodModel.id == command.period_id)
        )
        period = period_result.scalar_one_or_none()
        if period is None:
            raise ValueError(f"Payroll period {command.period_id} was not found.")

        taxable_income_result = await self._session.execute(
            select(func.coalesce(func.sum(PayrollItemModel.amount_clp), 0))
            .join(PayrollConceptModel, PayrollItemModel.concept_id == PayrollConceptModel.id)
            .where(PayrollItemModel.period_id == period.id)
            .where(PayrollConceptModel.kind == PayrollConceptKind.INCOME)
            .where(PayrollConceptModel.is_taxable.is_(True))
        )
        deductible_result = await self._session.execute(
            select(func.coalesce(func.sum(PayrollItemModel.amount_clp), 0))
            .join(PayrollConceptModel, PayrollItemModel.concept_id == PayrollConceptModel.id)
            .where(PayrollItemModel.period_id == period.id)
            .where(
                PayrollConceptModel.code.in_(
                    [
                        "PENSION_BASE",
                        "PENSION_ADDITIONAL",
                        "HEALTH_BASE",
                        "HEALTH_ADDITIONAL_UF",
                        "UNEMPLOYMENT_INSURANCE",
                    ]
                )
            )
        )

        return IncomeTaxContextDTO(
            period_id=period.id,
            payment_date=period.payment_date,
            taxable_income_clp=Decimal(taxable_income_result.scalar_one()),
            deductible_amount_clp=Decimal(deductible_result.scalar_one()),
        )

    async def get_income_tax_bracket(self, payment_date: date, taxable_base_utm: Decimal) -> IncomeTaxBracket | None:
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
            .order_by(IncomeTaxBracketModel.valid_from.desc(), IncomeTaxBracketModel.lower_bound_utm.desc())
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
        period_result = await self._session.execute(
            select(PayrollPeriodModel).where(PayrollPeriodModel.id == result.period_id)
        )
        period = period_result.scalar_one_or_none()
        if period is None:
            raise ValueError(f"Payroll period {result.period_id} was not found.")

        concept_result = await self._session.execute(
            select(PayrollConceptModel).where(PayrollConceptModel.code == "INCOME_TAX")
        )
        concept = concept_result.scalar_one_or_none()
        if concept is None:
            raise ValueError("Missing payroll concept for computed income tax: INCOME_TAX")

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

        await self._session.commit()
        await self._session.execute(text("REFRESH MATERIALIZED VIEW mv_payroll_summary"))
        await self._session.commit()
        return result
