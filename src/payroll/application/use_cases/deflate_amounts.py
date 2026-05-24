"""Use case for deflating payroll summary amounts with IPC."""

from payroll.application.errors import EconomicIndexNotFoundError, PayrollSummaryNotFoundError
from payroll.application.dto import DeflateAmountsCommandDTO, DeflateAmountsResultDTO, DeflatedAmountDTO
from payroll.application.ports.repositories import MarketDataRepository, PayrollRepository
from payroll.domain.deflation import DeflationCalculator


class DeflateAmounts:
    """Converts payroll summary nominal amounts into real CLP values."""

    def __init__(
        self,
        payroll_repository: PayrollRepository,
        market_data_repository: MarketDataRepository,
        calculator: DeflationCalculator | None = None,
    ) -> None:
        """Initialize the instance."""
        self._payroll_repository = payroll_repository
        self._market_data_repository = market_data_repository
        self._calculator = calculator or DeflationCalculator()

    async def execute(self, command: DeflateAmountsCommandDTO) -> DeflateAmountsResultDTO:
        """Handle execute."""
        detail = await self._payroll_repository.get_period_detail(command.period_id)
        if detail is None or detail.summary is None:
            raise PayrollSummaryNotFoundError(f"Payroll summary for period {command.period_id} was not found.")

        source_index = await self._market_data_repository.get_economic_index_value(
            command.index_code,
            detail.period_year,
            detail.period_month,
        )
        if source_index is None:
            raise EconomicIndexNotFoundError(
                f"Economic index {command.index_code} for {detail.period_year:04d}-{detail.period_month:02d} was not found."
            )

        target_index = await self._market_data_repository.get_economic_index_value(
            command.index_code,
            command.target_year,
            command.target_month,
        )
        if target_index is None:
            raise EconomicIndexNotFoundError(
                f"Economic index {command.index_code} for {command.target_year:04d}-{command.target_month:02d} was not found."
            )

        summary = detail.summary
        return DeflateAmountsResultDTO(
            period_id=summary.period_id,
            index_code=command.index_code,
            source_year=summary.period_year,
            source_month=summary.period_month,
            target_year=command.target_year,
            target_month=command.target_month,
            source_index_value=source_index,
            target_index_value=target_index,
            taxable_income=DeflatedAmountDTO(
                nominal_clp=summary.taxable_income_clp,
                real_clp=self._calculator.deflate_amount(summary.taxable_income_clp, source_index, target_index),
            ),
            gross_income=DeflatedAmountDTO(
                nominal_clp=summary.gross_income_clp,
                real_clp=self._calculator.deflate_amount(summary.gross_income_clp, source_index, target_index),
            ),
            total_discounts=DeflatedAmountDTO(
                nominal_clp=summary.total_discounts_clp,
                real_clp=self._calculator.deflate_amount(summary.total_discounts_clp, source_index, target_index),
            ),
            net_pay=DeflatedAmountDTO(
                nominal_clp=summary.net_pay_clp,
                real_clp=self._calculator.deflate_amount(summary.net_pay_clp, source_index, target_index),
            ),
        )
