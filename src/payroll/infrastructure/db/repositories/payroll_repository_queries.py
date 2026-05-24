"""Query-oriented payroll repository operations."""

from sqlalchemy import select

from payroll.application.dto import (
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)
from payroll.infrastructure.db.models import (
    EmployerModel,
    PayrollConceptModel,
    PayrollSummaryModel,
)
from payroll.infrastructure.db.models.payroll import (
    PayrollItemModel,
    PayrollPeriodModel,
)
from payroll.infrastructure.db.repositories.payroll_repository_shared import (
    SqlAlchemyPayrollRepositoryBase,
    build_payroll_summary_dto,
)


class SqlAlchemyPayrollQueryRepository(SqlAlchemyPayrollRepositoryBase):
    """Read-only payroll queries."""

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO | None:
        """Get period detail."""
        period_result = await self._session.execute(
            select(PayrollPeriodModel, EmployerModel)
            .join(EmployerModel, PayrollPeriodModel.employer_id == EmployerModel.id)
            .where(PayrollPeriodModel.id == period_id)
        )
        period_row = period_result.first()
        if period_row is None:
            return None
        period, employer = period_row
        employer_ended_at = await self._get_effective_employer_ended_at(employer)

        items_result = await self._session.execute(
            select(PayrollItemModel, PayrollConceptModel)
            .join(
                PayrollConceptModel,
                PayrollItemModel.concept_id == PayrollConceptModel.id,
            )
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
            summary = build_payroll_summary_dto(
                summary_model,
                employer_name=summary_employer.name,
                period=period,
            )

        return PayrollPeriodDetailDTO(
            id=period.id,
            employer_id=employer.id,
            employer_name=employer.name,
            employer_tax_id=employer.tax_id,
            employer_country_code=employer.country_code,
            employer_started_at=employer.started_at,
            employer_ended_at=employer_ended_at,
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
        """List period summaries."""
        result = await self._session.execute(
            select(PayrollSummaryModel, EmployerModel, PayrollPeriodModel)
            .join(EmployerModel, PayrollSummaryModel.employer_id == EmployerModel.id)
            .join(
                PayrollPeriodModel,
                PayrollSummaryModel.period_id == PayrollPeriodModel.id,
            )
            .order_by(
                PayrollSummaryModel.period_year.desc(),
                PayrollSummaryModel.period_month.desc(),
                EmployerModel.name,
            )
        )
        return [
            build_payroll_summary_dto(
                summary,
                employer_name=employer.name,
                period=period,
            )
            for summary, employer, period in result.all()
        ]
