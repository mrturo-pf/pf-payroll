"""Integration test: ComputeContributions → PfRatesClient → respx-mocked pf-rates.

Verifies that the real HTTP adapter wires correctly into the use case:
the UF value is fetched from pf-rates exactly once, parsed as Decimal,
and flows into the contribution math.
"""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from payroll.application.dto import (
    ComputeContributionsCommandDTO,
    ComputeContributionsResultDTO,
    ContributionComputationContextDTO,
)
from payroll.application.use_cases.compute_contributions import ComputeContributions
from payroll.domain.contributions import (
    ContributionCap,
    EmploymentContractKind,
    HealthInstitution,
    HealthInstitutionKind,
    HealthPlan,
    PensionInstitution,
    PensionPlan,
)
from payroll.infrastructure.http.pf_rates_client import PfRatesClient

_PF_RATES_BASE = "http://pf-rates.integration-test"


class StubPayrollRepository:
    """Test double for payroll persistence in pf-rates integration tests."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.saved: ComputeContributionsResultDTO | None = None

    async def get_contribution_context(
        self, command: ComputeContributionsCommandDTO
    ) -> ContributionComputationContextDTO:
        """Return an ISAPRE context whose contracted_clp depends on the fetched UF."""
        return ContributionComputationContextDTO(
            period_id=command.period_id,
            payment_date=date(2026, 1, 31),
            taxable_income_clp=Decimal("1000000"),
            employment_contract_kind=EmploymentContractKind.INDEFINITE,
            pension_plan=PensionPlan(
                id=1,
                institution=PensionInstitution("AFP_UNO", "AFP Uno", Decimal("0.10")),
                valid_from=date(2026, 1, 1),
                valid_to=None,
                additional_rate=Decimal("0.0127"),
            ),
            health_plan=HealthPlan(
                id=2,
                institution=HealthInstitution(
                    "BANMEDICA",
                    "Banmedica",
                    HealthInstitutionKind.ISAPRE,
                    Decimal("0.07"),
                ),
                valid_from=date(2026, 1, 1),
                valid_to=None,
                plan_name="Plan Oro",
                contracted_uf=Decimal("8.1000"),
            ),
            cap=ContributionCap(
                "pension_health", date(2026, 1, 1), None, Decimal("90.0000")
            ),
            unemployment_cap=ContributionCap(
                "unemployment", date(2026, 1, 1), None, Decimal("90.0000")
            ),
        )

    async def save_computed_contributions(
        self, result: ComputeContributionsResultDTO
    ) -> ComputeContributionsResultDTO:
        """Capture and return the computed result."""
        self.saved = result
        return result


@pytest.mark.asyncio
@respx.mock
async def test_compute_contributions_fetches_uf_from_pf_rates() -> None:
    """Compute contributions retrieves UF from pf-rates and produces correct amounts.

    Scenario (amounts proven correct in test_compute_contributions.py):
    - taxable_income = 1,000,000 CLP
    - UF = 40,000 CLP (mocked pf-rates response)
    - Banmedica ISAPRE, contracted_uf = 8.1 → contracted_clp = 324,000
    - caps = 90.0 UF (income is below cap, so no capping applies)

    uf_value_clp is omitted from the command so the use case must call
    PfRatesClient.get_exchange_rate_value.
    """
    uf_route = respx.get(f"{_PF_RATES_BASE}/exchange-rates/value").mock(
        return_value=httpx.Response(200, json={"value_clp": "40000.00"})
    )

    repository = StubPayrollRepository()
    client = PfRatesClient(_PF_RATES_BASE, "test-key", cache_ttl_seconds=60)
    use_case = ComputeContributions(repository, client)

    result = await use_case.execute(
        # No uf_value_clp: use case must call PfRatesClient
        ComputeContributionsCommandDTO(period_id=1, pension_plan_id=1, health_plan_id=2)
    )

    # The HTTP seam was exercised exactly once
    assert uf_route.call_count == 1

    # Amounts match the known-correct scenario proven in test_compute_contributions.py
    assert result.pension.base_amount_clp == Decimal("100000")  # 10% of 1,000,000
    assert result.pension.additional_amount_clp == Decimal(
        "12700"
    )  # 1.27% of 1,000,000
    assert result.health.base_amount_clp == Decimal("70000")  # 7% of 1,000,000
    assert result.health.contracted_clp == Decimal("324000")  # 8.1 × 40,000
    assert result.health.additional_amount_clp == Decimal("254000")  # 324,000 − 70,000
    assert result.unemployment.employee_amount_clp == Decimal(
        "6000"
    )  # 0.6% of 1,000,000
    assert result.unemployment.employer_amount_clp == Decimal(
        "24000"
    )  # 2.4% of 1,000,000
    assert repository.saved is result
