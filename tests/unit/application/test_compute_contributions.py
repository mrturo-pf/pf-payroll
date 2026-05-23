from datetime import date
from decimal import Decimal

import pytest

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


class StubPayrollRepository:
    def __init__(self) -> None:
        self.saved: ComputeContributionsResultDTO | None = None

    async def get_contribution_context(
        self,
        command: ComputeContributionsCommandDTO,
    ) -> ContributionComputationContextDTO:
        assert command.period_id == 10
        assert command.pension_plan_id == 1
        assert command.health_plan_id == 2
        return ContributionComputationContextDTO(
            period_id=10,
            payment_date=date(2026, 1, 31),
            taxable_income_clp=Decimal("1000000"),
            employment_contract_kind=EmploymentContractKind.INDEFINITE,
            pension_plan=PensionPlan(
                id=1,
                institution=PensionInstitution(
                    code="AFP_UNO",
                    name="AFP Uno",
                    mandatory_rate=Decimal("0.10"),
                ),
                valid_from=date(2026, 1, 1),
                valid_to=None,
                additional_rate=Decimal("0.0127"),
            ),
            health_plan=HealthPlan(
                id=2,
                institution=HealthInstitution(
                    code="BANMEDICA",
                    name="Banmedica",
                    kind=HealthInstitutionKind.ISAPRE,
                    mandatory_rate=Decimal("0.07"),
                ),
                valid_from=date(2026, 1, 1),
                valid_to=None,
                plan_name="Plan Oro",
                contracted_uf=Decimal("8.1000"),
            ),
            cap=ContributionCap(
                cap_type="pension_health",
                valid_from=date(2026, 1, 1),
                valid_to=None,
                value_uf=Decimal("90.0000"),
            ),
            unemployment_cap=ContributionCap(
                cap_type="unemployment",
                valid_from=date(2026, 1, 1),
                valid_to=None,
                value_uf=Decimal("90.0000"),
            ),
        )

    async def save_computed_contributions(
        self,
        result: ComputeContributionsResultDTO,
    ) -> ComputeContributionsResultDTO:
        self.saved = result
        return result


class StubMarketDataRepository:
    def __init__(self, uf_value: Decimal | None = Decimal("35000")) -> None:
        self.uf_value = uf_value
        self.lookups: list[tuple[str, date]] = []

    async def list_exchange_rates(self, currency_code: str | None = None) -> list[object]:
        raise AssertionError("not used")

    async def list_economic_indices(self, code: str | None = None) -> list[object]:
        raise AssertionError("not used")

    async def get_exchange_rate_value(self, currency_code: str, rate_date: date) -> Decimal | None:
        self.lookups.append((currency_code, rate_date))
        return self.uf_value

    async def refresh_rates(self, command: object) -> object:
        raise AssertionError("not used")


@pytest.mark.asyncio
async def test_compute_contributions_uses_domain_calculator_and_persists_result() -> None:
    repository = StubPayrollRepository()
    use_case = ComputeContributions(repository, StubMarketDataRepository())  # type: ignore[arg-type]

    result = await use_case.execute(
        ComputeContributionsCommandDTO(
            period_id=10,
            pension_plan_id=1,
            health_plan_id=2,
            uf_value_clp=Decimal("35000"),
        )
    )

    assert result.pension.base_amount_clp == Decimal("100000")
    assert result.pension.additional_amount_clp == Decimal("12700")
    assert result.health.base_amount_clp == Decimal("70000")
    assert result.health.contracted_clp == Decimal("283500")
    assert result.health.additional_amount_clp == Decimal("213500")
    assert result.unemployment.employee_amount_clp == Decimal("6000")
    assert result.unemployment.employer_amount_clp == Decimal("24000")
    assert result.total_discount_clp == Decimal("402200")
    assert repository.saved == result


@pytest.mark.asyncio
async def test_compute_contributions_uses_stored_uf_when_request_value_is_missing() -> None:
    repository = StubPayrollRepository()
    market_data_repository = StubMarketDataRepository(Decimal("40000"))
    use_case = ComputeContributions(repository, market_data_repository)  # type: ignore[arg-type]

    result = await use_case.execute(
        ComputeContributionsCommandDTO(
            period_id=10,
            pension_plan_id=1,
            health_plan_id=2,
        )
    )

    assert market_data_repository.lookups == [("UF", date(2026, 1, 31))]
    assert result.health.contracted_clp == Decimal("324000")
    assert result.unemployment.employee_amount_clp == Decimal("6000")


@pytest.mark.asyncio
async def test_compute_contributions_requires_stored_uf_when_not_provided() -> None:
    with pytest.raises(ValueError, match="UF exchange rate for 2026-01-31 was not found."):
        await ComputeContributions(
            StubPayrollRepository(),
            StubMarketDataRepository(None),  # type: ignore[arg-type]
        ).execute(
            ComputeContributionsCommandDTO(
                period_id=10,
                pension_plan_id=1,
                health_plan_id=2,
            )
        )
