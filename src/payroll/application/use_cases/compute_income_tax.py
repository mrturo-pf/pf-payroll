"""Use case for computing Chilean monthly income tax."""

from decimal import Decimal

from payroll.application.errors import IncomeTaxBracketNotFoundError
from payroll.application.dto import ComputeIncomeTaxCommandDTO, ComputeIncomeTaxResultDTO
from payroll.application.ports.repositories import MarketDataRepository, PayrollRepository
from payroll.application.services.exchange_rates import resolve_required_exchange_rate
from payroll.domain.tax_calculator import ChileanTaxCalculator, quantize_utm


class ComputeIncomeTax:
    """Computes and persists income tax withholding for a payroll period."""

    def __init__(
        self,
        repository: PayrollRepository,
        market_data_repository: MarketDataRepository,
        calculator: ChileanTaxCalculator | None = None,
    ) -> None:
        self._repository = repository
        self._market_data_repository = market_data_repository
        self._calculator = calculator or ChileanTaxCalculator()

    async def execute(self, command: ComputeIncomeTaxCommandDTO) -> ComputeIncomeTaxResultDTO:
        context = await self._repository.get_income_tax_context(command)
        utm_value_clp = await resolve_required_exchange_rate(
            provided_value=command.utm_value_clp,
            currency_code="UTM",
            rate_date=context.payment_date,
            market_data_repository=self._market_data_repository,
        )

        taxable_base_clp = max(Decimal("0"), context.taxable_income_clp - context.deductible_amount_clp)
        taxable_base_utm = quantize_utm(taxable_base_clp / utm_value_clp) if utm_value_clp > 0 else Decimal("0")
        bracket = await self._repository.get_income_tax_bracket(context.payment_date, taxable_base_utm)
        if bracket is None:
            raise IncomeTaxBracketNotFoundError(
                "No income tax bracket was found "
                f"for {context.payment_date.isoformat()} and taxable base {taxable_base_utm} UTM."
            )

        tax = self._calculator.income_tax(
            taxable_income_clp=context.taxable_income_clp,
            deductible_amount_clp=context.deductible_amount_clp,
            bracket=bracket,
            utm_value_clp=utm_value_clp,
        )
        return await self._repository.save_computed_income_tax(
            ComputeIncomeTaxResultDTO(
                period_id=context.period_id,
                tax=tax,
            )
        )
