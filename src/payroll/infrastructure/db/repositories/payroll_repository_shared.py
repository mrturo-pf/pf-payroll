"""Shared helpers for SQLAlchemy payroll repositories."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from payroll.application.ports.rate_provider import FxRateProvider
from payroll.application.errors import (
    HealthPlanNotFoundError,
    PayrollConflictError,
    PayrollNotFoundError,
    PayrollPeriodNotFoundError,
    PensionPlanNotFoundError,
)
from payroll.application.dto import PayrollSummaryDTO
from payroll.infrastructure.db.models import (
    ContributionCapModel,
    EmployerModel,
    HealthInstitutionModel,
    HealthPlanModel,
    PayrollConceptModel,
    PensionInstitutionModel,
    PensionPlanModel,
    PayrollSummaryModel,
)
from payroll.infrastructure.db.models.payroll import (
    PayrollItemModel,
    PayrollPeriodModel,
)
from payroll.infrastructure.db.models.reference_data import ContributionCapType
from payroll.shared.constants import REVIEW_REQUIRED_CONCEPT_CODES


def build_net_pay_warning(
    declared_net_pay_clp: Decimal | None,
    expected_net_pay_clp: Decimal | None,
    net_pay_difference_clp: Decimal | None,
) -> str | None:
    """Build net pay warning."""
    if declared_net_pay_clp is None:
        return None
    if expected_net_pay_clp is None or net_pay_difference_clp is None:
        return (
            "Declared net_pay will be reconciled after computed contributions "
            "and income tax are generated."
        )
    if net_pay_difference_clp == 0:
        return None
    return (
        "Declared net_pay does not match the fully computed payroll totals. "
        f"Difference: {net_pay_difference_clp} CLP."
    )


def get_last_day_of_month(target_date: date) -> date:
    """Get the last day of the month for the given date."""
    last_day = monthrange(target_date.year, target_date.month)[1]
    return date(target_date.year, target_date.month, last_day)


def _build_fx_provider() -> FxRateProvider:
    """Build a chained FX provider for runtime UF fallbacks."""
    from payroll.interfaces.api.dependencies import get_fx_rate_provider

    return get_fx_rate_provider()


async def _resolve_uf_value(
    *,
    session: AsyncSession,
    target_date: date,
    fx_provider: FxRateProvider | None = None,
    allow_provider_lookup: bool = True,
) -> Decimal | None:
    """Resolve UF rate using DB, provider, today's provider value, then latest DB."""
    from payroll.infrastructure.db.models import ExchangeRateModel

    exact_result = await session.execute(
        select(ExchangeRateModel.value_clp)
        .where(ExchangeRateModel.currency_code == "UF")
        .where(ExchangeRateModel.rate_date == target_date)
    )
    exact_value = exact_result.scalar_one_or_none()
    if exact_value is not None and exact_value > 0:
        return exact_value

    if allow_provider_lookup and fx_provider is not None:
        target_provider_value = await fx_provider.fetch_rate("UF", target_date)
        if target_provider_value is not None and target_provider_value > 0:
            return target_provider_value

        today_provider_value = await fx_provider.fetch_rate("UF", date.today())
        if today_provider_value is not None and today_provider_value > 0:
            return today_provider_value

    latest_result = await session.execute(
        select(ExchangeRateModel.value_clp)
        .where(ExchangeRateModel.currency_code == "UF")
        .where(ExchangeRateModel.rate_date >= date.today())
        .where(ExchangeRateModel.rate_date <= target_date)
        .order_by(ExchangeRateModel.rate_date.desc())
        .limit(1)
    )
    latest_value = latest_result.scalar_one_or_none()
    if latest_value is None or latest_value <= 0:
        return None
    return latest_value


async def predict_next_period_net_pay(
    session: AsyncSession,
    current_period: PayrollPeriodModel,
    current_period_end_month: date,
    fx_provider: FxRateProvider | None = None,
    allow_provider_lookup: bool = True,
) -> Decimal | None:
    """Predict net_pay_clp for the next period based on current period data.

    Uses income items (SALARY_BASE, LEGAL_GRATUITY, TELEWORK_REFUND) from
    the current period and applies the same discount ratios for non-UF
    discounts.

    Recalculates UF-based discount concepts (HEALTH_ADDITIONAL_UF) and
    HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION using the selected UF tied to
    the current period's end_date month-end.

    Adjusts income to 30-day accounting month if current period had fewer days.

    Args:
        session: Database session
        current_period: The current payroll period
        current_period_end_month: Date in month to extract UF from (e.g., end_date)

    Returns:
        Predicted net_pay_clp for the next period, or None if calculation fails
    """
    # Resolve UF using last day of current period's end_date month
    last_day_current_month = get_last_day_of_month(current_period_end_month)

    resolved_fx_provider = (
        (fx_provider or _build_fx_provider()) if allow_provider_lookup else None
    )
    uf_current = await _resolve_uf_value(
        session=session,
        target_date=last_day_current_month,
        fx_provider=resolved_fx_provider,
        allow_provider_lookup=allow_provider_lookup,
    )
    if uf_current is None:
        return None

    # Get current period's income and discount items
    items_result = await session.execute(
        select(PayrollItemModel.amount_clp, PayrollConceptModel.code)
        .join(
            PayrollConceptModel,
            PayrollItemModel.concept_id == PayrollConceptModel.id,
        )
        .where(PayrollItemModel.period_id == current_period.id)
    )
    items = items_result.all()

    # Extract specific income components
    income_codes = {"SALARY_BASE", "LEGAL_GRATUITY", "TELEWORK_REFUND"}
    non_uf_discount_codes = {
        "PENSION_BASE",
        "PENSION_ADDITIONAL",
        "HEALTH_BASE",
        "HEALTH_INSURANCE",
        "UNEMPLOYMENT_INSURANCE",
        "INCOME_TAX",
    }
    uf_discount_codes = {"HEALTH_ADDITIONAL_UF"}

    future_gross = Decimal("0")
    current_gross = Decimal("0")
    current_non_uf_discounts = Decimal("0")
    current_uf_discounts = Decimal("0")
    employer_health_insurance_clp = Decimal("0")

    for amount, code in items:
        if code in income_codes:
            future_gross += amount
            current_gross += amount
        elif code in non_uf_discount_codes:
            current_non_uf_discounts += amount
        elif code in uf_discount_codes:
            current_uf_discounts += amount
        elif code == "HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION":
            employer_health_insurance_clp += amount

    # If no income or gross income is zero, cannot predict
    if future_gross <= 0 or current_gross <= 0:
        return None

    reference_uf_for_current: Decimal | None = uf_current
    if current_uf_discounts > 0 or employer_health_insurance_clp > 0:
        from payroll.infrastructure.db.models import ExchangeRateModel

        reference_result = await session.execute(
            select(ExchangeRateModel.value_clp)
            .where(ExchangeRateModel.currency_code == "UF")
            .where(ExchangeRateModel.rate_date == current_period.payment_date)
        )
        reference_uf_for_current = reference_result.scalar_one_or_none()
        if reference_uf_for_current is None or reference_uf_for_current <= 0:
            latest_reference_result = await session.execute(
                select(ExchangeRateModel.value_clp)
                .where(ExchangeRateModel.currency_code == "UF")
                .where(ExchangeRateModel.rate_date <= current_period.payment_date)
                .order_by(ExchangeRateModel.rate_date.desc())
                .limit(1)
            )
            latest_reference = latest_reference_result.scalar_one_or_none()
            if latest_reference is not None and latest_reference > 0:
                reference_uf_for_current = latest_reference

    if reference_uf_for_current is None or reference_uf_for_current <= 0:
        return None

    # Recalculate employer contribution using selected UF (from current end_date month)
    # Convert from CLP (current UF) -> UF quantity -> CLP (selected UF)
    if employer_health_insurance_clp > 0:
        employer_uf_quantity = employer_health_insurance_clp / reference_uf_for_current
        employer_contribution_future = employer_uf_quantity * uf_current
        future_gross += employer_contribution_future
        current_gross += employer_health_insurance_clp

    future_uf_discounts = Decimal("0")
    if current_uf_discounts > 0:
        health_uf_quantity = current_uf_discounts / reference_uf_for_current
        future_uf_discounts = health_uf_quantity * uf_current

    # Adjust income and discounts to 30-day accounting month if needed
    worked_days = current_period.worked_days or 30
    non_uf_discount_ratio = Decimal("0")

    if current_gross > 0:
        # Calculate non-UF discount ratio from current period
        non_uf_discount_ratio = current_non_uf_discounts / current_gross

    if worked_days > 0 and worked_days < 30:
        # Project income to 30 days
        current_gross = current_gross * Decimal(30) / Decimal(worked_days)
        future_gross = future_gross * Decimal(30) / Decimal(worked_days)

    # Apply same ratio to future gross for non-UF discounts and
    # add UF-derived discounts converted with selected UF.
    future_non_uf_discounts = future_gross * non_uf_discount_ratio
    future_discounts = future_non_uf_discounts + future_uf_discounts

    # Calculate predicted net pay
    predicted_net_pay = future_gross - future_discounts

    if predicted_net_pay <= 0:
        return None

    # Quantize to 2 decimal places (CLP cents)
    return predicted_net_pay.quantize(Decimal("0.01"))


def build_payroll_summary_dto(
    summary: PayrollSummaryModel,
    *,
    employer_name: str,
    period: PayrollPeriodModel,
) -> PayrollSummaryDTO:
    """Build payroll summary dto."""
    return PayrollSummaryDTO(
        period_id=summary.period_id,
        employer_id=summary.employer_id,
        employer_name=employer_name,
        period_year=summary.period_year,
        period_month=summary.period_month,
        payment_date=summary.payment_date,
        taxable_income_clp=summary.taxable_income_clp,
        gross_income_clp=summary.gross_income_clp,
        total_discounts_clp=summary.total_discounts_clp,
        net_pay_clp=summary.net_pay_clp,
        declared_net_pay_clp=period.declared_net_pay_clp,
        expected_net_pay_clp=period.expected_net_pay_clp,
        net_pay_difference_clp=period.net_pay_difference_clp,
        net_pay_warning=build_net_pay_warning(
            period.declared_net_pay_clp,
            period.expected_net_pay_clp,
            period.net_pay_difference_clp,
        ),
    )


class SqlAlchemyPayrollRepositoryBase:
    """Common helpers shared across payroll repository concerns."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the instance."""
        self._session = session

    async def _refresh_summary_view(self) -> None:
        """Handle refresh summary view."""
        await self._session.commit()
        await self._session.execute(
            text("REFRESH MATERIALIZED VIEW mv_payroll_summary")
        )
        await self._session.commit()

    async def _reconcile_period_net_pay(
        self,
        period: PayrollPeriodModel,
        *,
        refresh_summary_view: bool,
    ) -> None:
        """Reconcile declared net pay once all computed concepts exist."""
        if refresh_summary_view:
            await self._refresh_summary_view()

        if period.declared_net_pay_clp is None:
            period.expected_net_pay_clp = None
            period.net_pay_difference_clp = None
            await self._session.commit()
            return

        concept_result = await self._session.execute(
            select(PayrollConceptModel.code)
            .join(
                PayrollItemModel,
                PayrollItemModel.concept_id == PayrollConceptModel.id,
            )
            .where(PayrollItemModel.period_id == period.id)
            .where(PayrollConceptModel.code.in_(REVIEW_REQUIRED_CONCEPT_CODES))
        )
        available_codes = set(concept_result.scalars().all())
        if available_codes != REVIEW_REQUIRED_CONCEPT_CODES:
            period.expected_net_pay_clp = None
            period.net_pay_difference_clp = None
            await self._session.commit()
            return

        summary_result = await self._session.execute(
            select(PayrollSummaryModel.net_pay_clp).where(
                PayrollSummaryModel.period_id == period.id
            )
        )
        expected_net_pay_clp = summary_result.scalar_one_or_none()
        if expected_net_pay_clp is None:
            period.expected_net_pay_clp = None
            period.net_pay_difference_clp = None
            await self._session.commit()
            return

        period.expected_net_pay_clp = Decimal(expected_net_pay_clp)
        period.net_pay_difference_clp = (
            period.declared_net_pay_clp - period.expected_net_pay_clp
        )
        await self._session.commit()

    async def _get_latest_contribution_cap(
        self,
        *,
        cap_type: ContributionCapType,
        payment_date: date,
        missing_message: str,
    ) -> ContributionCapModel:
        """Handle get latest contribution cap."""
        result = await self._session.execute(
            select(ContributionCapModel)
            .where(ContributionCapModel.cap_type == cap_type)
            .where(ContributionCapModel.valid_from <= payment_date)
            .where(
                or_(
                    ContributionCapModel.valid_to.is_(None),
                    ContributionCapModel.valid_to >= payment_date,
                )
            )
            .order_by(ContributionCapModel.valid_from.desc())
            .limit(1)
        )
        cap_model = result.scalar_one_or_none()
        if cap_model is None:
            raise PayrollNotFoundError(missing_message)
        return cap_model

    async def _get_period(self, period_id: int) -> PayrollPeriodModel:
        """Handle get period."""
        period_result = await self._session.execute(
            select(PayrollPeriodModel).where(PayrollPeriodModel.id == period_id)
        )
        period = period_result.scalar_one_or_none()
        if period is None:
            raise PayrollPeriodNotFoundError(
                f"Payroll period {period_id} was not found."
            )
        return period

    async def _get_effective_employer_ended_at(
        self, employer: EmployerModel
    ) -> date | None:
        """Resolve the effective employer end date."""
        if employer.ended_at is not None:
            return employer.ended_at

        result = await self._session.execute(
            select(EmployerModel.started_at)
            .where(EmployerModel.id != employer.id)
            .where(EmployerModel.started_at > employer.started_at)
            .order_by(EmployerModel.started_at.asc())
            .limit(1)
        )
        next_started_at = result.scalar_one_or_none()
        if next_started_at is None:
            return None
        return next_started_at - timedelta(days=1)

    async def _close_overlapping_open_ended_employers(
        self, employer: EmployerModel
    ) -> None:
        """Close previous open-ended employers that overlap the new employer."""
        result = await self._session.execute(
            select(EmployerModel)
            .where(EmployerModel.id != employer.id)
            .where(EmployerModel.started_at < employer.started_at)
            .where(EmployerModel.ended_at.is_(None))
        )
        inferred_end_date = employer.started_at - timedelta(days=1)
        for overlapping_employer in result.scalars().all():
            overlapping_employer.ended_at = inferred_end_date

    async def _get_pension_plan(
        self,
        plan_id: int,
        payment_date: date,
    ) -> tuple[PensionPlanModel, PensionInstitutionModel]:
        """Handle get pension plan."""
        pension_result = await self._session.execute(
            select(PensionPlanModel, PensionInstitutionModel)
            .join(
                PensionInstitutionModel,
                PensionPlanModel.institution_id == PensionInstitutionModel.id,
            )
            .where(PensionPlanModel.id == plan_id)
        )
        pension_row = pension_result.first()
        if pension_row is None:
            raise PensionPlanNotFoundError(f"Pension plan {plan_id} was not found.")

        pension_plan_model, pension_institution_model = pension_row
        if pension_plan_model.valid_from > payment_date or (
            pension_plan_model.valid_to is not None
            and pension_plan_model.valid_to < payment_date
        ):
            raise PayrollConflictError(
                f"Pension plan {plan_id} is not valid for {payment_date.isoformat()}."
            )

        return pension_plan_model, pension_institution_model

    async def _get_health_plan(
        self,
        plan_id: int,
        payment_date: date,
        *,
        require_active: bool = False,
    ) -> tuple[HealthPlanModel, HealthInstitutionModel]:
        """Handle get health plan."""
        health_result = await self._session.execute(
            select(HealthPlanModel, HealthInstitutionModel)
            .join(
                HealthInstitutionModel,
                HealthPlanModel.institution_id == HealthInstitutionModel.id,
            )
            .where(HealthPlanModel.id == plan_id)
        )
        health_row = health_result.first()
        if health_row is None:
            raise HealthPlanNotFoundError(f"Health plan {plan_id} was not found.")

        health_plan_model, health_institution_model = health_row
        if require_active and not health_institution_model.is_active:
            raise PayrollConflictError(
                f"Health plan {plan_id} belongs to inactive health institution "
                f"{health_institution_model.code}."
            )
        if health_plan_model.valid_from > payment_date or (
            health_plan_model.valid_to is not None
            and health_plan_model.valid_to < payment_date
        ):
            raise PayrollConflictError(
                f"Health plan {plan_id} is not valid for {payment_date.isoformat()}."
            )

        return health_plan_model, health_institution_model
