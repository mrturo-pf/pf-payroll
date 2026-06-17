"""Shared contribution-computation helpers."""

from decimal import Decimal

from payroll.application.dto import (
    ComputeContributionsCommandDTO,
    ComputeContributionsResultDTO,
    ContributionComputationContextDTO,
    ImportedContributionValidationDTO,
    PayrollPeriodDetailDTO,
)
from payroll.application.ports.repositories import (
    MarketDataRepository,
    PayrollRepository,
)
from payroll.application.services.exchange_rates import (
    resolve_month_end_uf_exchange_rate,
)
from payroll.domain.contribution_calculator import ContributionCalculator

_PENSION_BASE_CODE = "PENSION_BASE"
_PENSION_ADDITIONAL_CODE = "PENSION_ADDITIONAL"
_HEALTH_BASE_CODE = "HEALTH_BASE"
_HEALTH_PLAN_ADDITIONAL_CODE = "HEALTH_ADDITIONAL_UF"
_VALIDATION_PENDING_WARNING = (
    "Contribution values will be reconciled after pension and health plans are "
    "assigned."
)


def _sum_concept_amount(
    detail: PayrollPeriodDetailDTO, concept_code: str
) -> Decimal | None:
    """Sum a concept amount from period detail items."""
    amount = sum(
        (item.amount_clp for item in detail.items if item.concept_code == concept_code),
        Decimal("0"),
    )
    return (
        None
        if amount == 0
        and not any(item.concept_code == concept_code for item in detail.items)
        else amount
    )


def build_imported_contribution_validation(
    detail: PayrollPeriodDetailDTO,
    computed: ComputeContributionsResultDTO | None,
) -> ImportedContributionValidationDTO | None:
    """Build imported contribution validation values."""
    declared_pension_base_clp = _sum_concept_amount(detail, _PENSION_BASE_CODE)
    declared_pension_additional_clp = _sum_concept_amount(
        detail, _PENSION_ADDITIONAL_CODE
    )
    declared_health_base_clp = _sum_concept_amount(detail, _HEALTH_BASE_CODE)
    declared_health_plan_additional_clp = _sum_concept_amount(
        detail, _HEALTH_PLAN_ADDITIONAL_CODE
    )

    if all(
        value is None
        for value in (
            declared_pension_base_clp,
            declared_pension_additional_clp,
            declared_health_base_clp,
            declared_health_plan_additional_clp,
        )
    ):
        return None

    if computed is None:
        return ImportedContributionValidationDTO(
            declared_pension_base_clp=declared_pension_base_clp,
            declared_pension_additional_clp=declared_pension_additional_clp,
            declared_health_base_clp=declared_health_base_clp,
            declared_health_plan_additional_clp=declared_health_plan_additional_clp,
            warning=_VALIDATION_PENDING_WARNING,
        )

    expected_pension_base_clp = computed.pension.base_amount_clp
    expected_pension_additional_clp = computed.pension.additional_amount_clp
    expected_health_base_clp = computed.health.base_amount_clp
    has_multiple_health_plan_snapshots = bool(
        detail.health_plan_ids and len(detail.health_plan_ids) > 1
    )
    expected_health_plan_additional_clp = (
        None
        if has_multiple_health_plan_snapshots
        else computed.health.additional_amount_clp
    )

    mismatches: list[str] = []
    if declared_pension_base_clp is not None and declared_pension_base_clp != (
        expected_pension_base_clp
    ):
        mismatches.append(
            f"PENSION_BASE declared {declared_pension_base_clp} CLP, "
            f"expected {expected_pension_base_clp} CLP."
        )
    if declared_pension_additional_clp is not None and (
        declared_pension_additional_clp != expected_pension_additional_clp
    ):
        mismatches.append(
            f"PENSION_ADDITIONAL declared {declared_pension_additional_clp} CLP, "
            f"expected {expected_pension_additional_clp} CLP."
        )
    if declared_health_base_clp is not None and declared_health_base_clp != (
        expected_health_base_clp
    ):
        mismatches.append(
            f"HEALTH_BASE declared {declared_health_base_clp} CLP, "
            f"expected {expected_health_base_clp} CLP."
        )
    if (
        declared_health_plan_additional_clp is not None
        and expected_health_plan_additional_clp is not None
        and declared_health_plan_additional_clp != expected_health_plan_additional_clp
    ):
        mismatches.append(
            "HEALTH_ADDITIONAL_UF declared "
            f"{declared_health_plan_additional_clp} CLP, expected "
            f"{expected_health_plan_additional_clp} CLP."
        )

    return ImportedContributionValidationDTO(
        declared_pension_base_clp=declared_pension_base_clp,
        expected_pension_base_clp=expected_pension_base_clp,
        pension_base_difference_clp=(
            None
            if declared_pension_base_clp is None
            else declared_pension_base_clp - expected_pension_base_clp
        ),
        declared_pension_additional_clp=declared_pension_additional_clp,
        expected_pension_additional_clp=expected_pension_additional_clp,
        pension_additional_difference_clp=(
            None
            if declared_pension_additional_clp is None
            else declared_pension_additional_clp - expected_pension_additional_clp
        ),
        declared_health_base_clp=declared_health_base_clp,
        expected_health_base_clp=expected_health_base_clp,
        health_base_difference_clp=(
            None
            if declared_health_base_clp is None
            else declared_health_base_clp - expected_health_base_clp
        ),
        declared_health_plan_additional_clp=declared_health_plan_additional_clp,
        expected_health_plan_additional_clp=expected_health_plan_additional_clp,
        health_plan_additional_difference_clp=(
            None
            if (
                declared_health_plan_additional_clp is None
                or expected_health_plan_additional_clp is None
            )
            else declared_health_plan_additional_clp
            - expected_health_plan_additional_clp
        ),
        warning=None
        if not mismatches
        else "Imported contribution totals do not match the computed payroll "
        f"contributions. {' '.join(mismatches)}",
    )


class _WithContributionCalculator:
    """Base for classes that need a calculator, payroll repo, and market-data repo."""

    def __init__(
        self,
        repository: PayrollRepository,
        market_data_repository: MarketDataRepository,
        calculator: ContributionCalculator | None = None,
    ) -> None:
        """Initialize the instance."""
        self._repository = repository
        self._market_data_repository = market_data_repository
        self._calculator = calculator or ContributionCalculator()


class ContributionComputationService(_WithContributionCalculator):
    """Compute payroll contributions without persisting them."""

    async def compute(
        self, command: ComputeContributionsCommandDTO
    ) -> ComputeContributionsResultDTO:
        """Compute a payroll contribution breakdown."""
        context: ContributionComputationContextDTO = (
            await self._repository.get_contribution_context(command)
        )
        month_end_uf_value_clp = await resolve_month_end_uf_exchange_rate(
            provided_value=command.uf_value_clp,
            payment_date=context.payment_date,
            market_data_repository=self._market_data_repository,
        )

        pension = self._calculator.pension(
            context.taxable_income_clp,
            context.pension_plan,
            context.cap,
            month_end_uf_value_clp,
        )
        health = self._calculator.health(
            context.taxable_income_clp,
            context.health_plan,
            context.cap,
            month_end_uf_value_clp,
            month_end_uf_value_clp,
        )
        unemployment = self._calculator.unemployment(
            context.taxable_income_clp,
            context.employment_contract_kind,
            context.unemployment_cap,
            month_end_uf_value_clp,
        )
        return ComputeContributionsResultDTO(
            period_id=context.period_id,
            pension_plan_id=context.pension_plan.id,
            health_plan_id=context.health_plan.id,
            taxable_income_clp=context.taxable_income_clp,
            pension=pension,
            health=health,
            unemployment=unemployment,
            total_discount_clp=(
                pension.base_amount_clp
                + pension.additional_amount_clp
                + health.base_amount_clp
                + health.additional_amount_clp
                + unemployment.employee_amount_clp
            ),
        )
