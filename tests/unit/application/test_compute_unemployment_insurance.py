"""Tests for compute unemployment insurance."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    ComputeUnemploymentInsuranceCommandDTO,
    ComputeUnemploymentInsuranceResultDTO,
    UnemploymentComputationContextDTO,
)
from payroll.application.use_cases.compute_unemployment_insurance import (
    ComputeUnemploymentInsurance,
)
from payroll.domain.contributions import ContributionCap, EmploymentContractKind
from helpers.market_data_stubs import UfLookupStubMixin


class StubPayrollRepository:
    """Test double for Payroll Repository."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.saved: ComputeUnemploymentInsuranceResultDTO | None = None

    async def get_unemployment_context(
        self, command: ComputeUnemploymentInsuranceCommandDTO
    ) -> UnemploymentComputationContextDTO:
        """Get unemployment context."""
        assert command.period_id == 10
        return UnemploymentComputationContextDTO(
            period_id=10,
            payment_date=date(2026, 1, 31),
            taxable_income_clp=Decimal("1000000"),
            employment_contract_kind=EmploymentContractKind.INDEFINITE,
            unemployment_cap=ContributionCap(
                cap_type="unemployment",
                valid_from=date(2026, 1, 1),
                valid_to=None,
                value_uf=Decimal("90.0000"),
            ),
        )

    async def save_computed_unemployment(
        self,
        result: ComputeUnemploymentInsuranceResultDTO,
    ) -> ComputeUnemploymentInsuranceResultDTO:
        """Save computed unemployment."""
        self.saved = result
        return result


class StubMarketDataRepository(UfLookupStubMixin):
    """Test double for Market Data Repository."""


@pytest.mark.asyncio
async def test_compute_unemployment_uses_stored_uf_and_persists_result() -> None:
    """Test compute unemployment uses stored uf and persists result."""
    repository = StubPayrollRepository()
    market_data_repository = StubMarketDataRepository()

    result = await ComputeUnemploymentInsurance(
        repository,
        market_data_repository,  # type: ignore[arg-type]
    ).execute(ComputeUnemploymentInsuranceCommandDTO(period_id=10))

    assert market_data_repository.lookups == [("UF", date(2026, 1, 31))]
    assert result.unemployment.employee_amount_clp == Decimal("6000")
    assert repository.saved == result


@pytest.mark.asyncio
async def test_compute_unemployment_rejects_missing_uf_value() -> None:
    """Test compute unemployment rejects missing uf value."""
    with pytest.raises(
        ValueError, match="UF exchange rate for 2026-01-31 was not found."
    ):
        await ComputeUnemploymentInsurance(
            StubPayrollRepository(),
            StubMarketDataRepository(None),  # type: ignore[arg-type]
        ).execute(ComputeUnemploymentInsuranceCommandDTO(period_id=10))


@pytest.mark.asyncio
async def test_compute_unemployment_uses_month_end_uf_for_cap() -> None:
    """Test compute unemployment uses month-end UF for the contribution cap."""

    class PriorDayRepository(StubPayrollRepository):
        """Test double for a non-month-end payment date."""

        async def get_unemployment_context(
            self, command: ComputeUnemploymentInsuranceCommandDTO
        ) -> UnemploymentComputationContextDTO:
            """Get unemployment context."""
            context = await super().get_unemployment_context(command)
            return UnemploymentComputationContextDTO(
                period_id=context.period_id,
                payment_date=date(2026, 1, 30),
                taxable_income_clp=Decimal("4000000"),
                employment_contract_kind=context.employment_contract_kind,
                unemployment_cap=context.unemployment_cap,
            )

    market_data_repository = StubMarketDataRepository(
        {
            date(2026, 1, 30): Decimal("40000"),
            date(2026, 1, 31): Decimal("41000"),
        }
    )
    result = await ComputeUnemploymentInsurance(
        PriorDayRepository(),
        market_data_repository,  # type: ignore[arg-type]
    ).execute(ComputeUnemploymentInsuranceCommandDTO(period_id=10))

    assert market_data_repository.lookups == [("UF", date(2026, 1, 31))]
    assert result.unemployment.cap_clp == Decimal("3690000")
    assert result.unemployment.capped_base_clp == Decimal("3690000")
    assert result.unemployment.employee_amount_clp == Decimal("22140")
    assert result.unemployment.employer_amount_clp == Decimal("88560")
