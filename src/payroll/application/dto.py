"""Application DTOs."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from payroll.domain.contributions import (
    ContributionCap,
    EmploymentContractKind,
    HealthContribution,
    HealthInstitutionKind,
    HealthPlan,
    PensionContribution,
    PensionPlan,
    UnemploymentContribution,
)
from payroll.domain.taxes import IncomeTaxComputation

PayrollConceptKind = Literal["income", "discount"]
PayrollStatusKind = Literal["projected", "actual", "reviewed"]


@dataclass(frozen=True, slots=True)
class MoneyDTO:
    amount: Decimal
    currency: str = "CLP"


@dataclass(frozen=True, slots=True)
class CurrencyDTO:
    code: str
    name: str
    is_fiat: bool
    unit_kind: str


@dataclass(frozen=True, slots=True)
class PensionInstitutionDTO:
    code: str
    name: str
    mandatory_rate: Decimal
    is_active: bool


@dataclass(frozen=True, slots=True)
class HealthInstitutionDTO:
    code: str
    name: str
    kind: HealthInstitutionKind
    mandatory_rate: Decimal
    is_active: bool


@dataclass(frozen=True, slots=True)
class PensionPlanDTO:
    id: int
    institution_code: str
    institution_name: str
    valid_from: date
    valid_to: date | None
    additional_rate: Decimal


@dataclass(frozen=True, slots=True)
class HealthPlanDTO:
    id: int
    institution_code: str
    institution_name: str
    institution_kind: HealthInstitutionKind
    valid_from: date
    valid_to: date | None
    plan_name: str | None
    contracted_uf: Decimal


@dataclass(frozen=True, slots=True)
class ContributionCapDTO:
    cap_type: str
    valid_from: date
    valid_to: date | None
    value_uf: Decimal


@dataclass(frozen=True, slots=True)
class ExchangeRateDTO:
    currency_code: str
    rate_date: date
    value_clp: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class EconomicIndexDTO:
    code: str
    period_year: int
    period_month: int
    index_value: Decimal
    monthly_change: Decimal | None
    yearly_change: Decimal | None
    base_period: str
    source: str


@dataclass(frozen=True, slots=True)
class PayrollConceptDTO:
    code: str
    name: str
    kind: PayrollConceptKind
    is_taxable: bool


@dataclass(frozen=True, slots=True)
class ImportPayrollRowDTO:
    employer: str
    period_year: int
    period_month: int
    payment_date: date
    status: PayrollStatusKind
    employment_contract_kind: EmploymentContractKind
    concept_code: str
    amount_clp: Decimal


@dataclass(frozen=True, slots=True)
class ImportedPayrollPeriodDTO:
    id: int
    employer: str
    period_year: int
    period_month: int
    payment_date: date
    status: PayrollStatusKind
    employment_contract_kind: EmploymentContractKind
    item_count: int


@dataclass(frozen=True, slots=True)
class ImportPayrollResultDTO:
    imported_periods: int
    imported_items: int
    periods: list[ImportedPayrollPeriodDTO]


@dataclass(frozen=True, slots=True)
class ComputeContributionsCommandDTO:
    period_id: int
    pension_plan_id: int
    health_plan_id: int
    uf_value_clp: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AssignPlansCommandDTO:
    period_id: int
    pension_plan_id: int
    health_plan_id: int


@dataclass(frozen=True, slots=True)
class AssignPlansResultDTO:
    period_id: int
    payment_date: date
    pension_plan_id: int
    health_plan_id: int


@dataclass(frozen=True, slots=True)
class ContributionComputationContextDTO:
    period_id: int
    payment_date: date
    taxable_income_clp: Decimal
    employment_contract_kind: EmploymentContractKind
    pension_plan: PensionPlan
    health_plan: HealthPlan
    cap: ContributionCap
    unemployment_cap: ContributionCap


@dataclass(frozen=True, slots=True)
class ComputeContributionsResultDTO:
    period_id: int
    pension_plan_id: int
    health_plan_id: int
    taxable_income_clp: Decimal
    pension: PensionContribution
    health: HealthContribution
    unemployment: UnemploymentContribution
    total_discount_clp: Decimal


@dataclass(frozen=True, slots=True)
class ExchangeRateWriteDTO:
    currency_code: str
    rate_date: date
    value_clp: Decimal
    source: str = "manual"


@dataclass(frozen=True, slots=True)
class EconomicIndexWriteDTO:
    code: str
    period_year: int
    period_month: int
    index_value: Decimal
    monthly_change: Decimal | None = None
    yearly_change: Decimal | None = None
    base_period: str = "DIC-2018"
    source: str = "manual"


@dataclass(frozen=True, slots=True)
class ProviderExchangeRateRequestDTO:
    currency_code: str
    rate_date: date


@dataclass(frozen=True, slots=True)
class ProviderEconomicIndexRequestDTO:
    code: str
    period_year: int
    period_month: int


@dataclass(frozen=True, slots=True)
class RefreshRatesCommandDTO:
    exchange_rates: list[ExchangeRateWriteDTO] = field(default_factory=list)
    economic_indices: list[EconomicIndexWriteDTO] = field(default_factory=list)
    provider_exchange_rates: list[ProviderExchangeRateRequestDTO] = field(default_factory=list)
    provider_economic_indices: list[ProviderEconomicIndexRequestDTO] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RefreshRatesResultDTO:
    upserted_exchange_rates: int
    upserted_economic_indices: int


@dataclass(frozen=True, slots=True)
class PayrollItemDetailDTO:
    concept_code: str
    concept_name: str
    kind: PayrollConceptKind
    is_taxable: bool
    amount_clp: Decimal
    notes: str | None


@dataclass(frozen=True, slots=True)
class PayrollSummaryDTO:
    period_id: int
    employer_id: int
    employer_name: str
    period_year: int
    period_month: int
    payment_date: date
    taxable_income_clp: Decimal
    gross_income_clp: Decimal
    total_discounts_clp: Decimal
    net_pay_clp: Decimal


@dataclass(frozen=True, slots=True)
class PayrollPeriodDetailDTO:
    id: int
    employer_id: int
    employer_name: str
    employer_tax_id: str | None
    employer_country_code: str
    period_year: int
    period_month: int
    payment_date: date
    worked_days: int
    status: PayrollStatusKind
    employment_contract_kind: EmploymentContractKind
    pension_plan_id: int | None
    health_plan_id: int | None
    items: list[PayrollItemDetailDTO]
    summary: PayrollSummaryDTO | None


@dataclass(frozen=True, slots=True)
class ComputeIncomeTaxCommandDTO:
    period_id: int
    utm_value_clp: Decimal | None = None


@dataclass(frozen=True, slots=True)
class IncomeTaxContextDTO:
    period_id: int
    payment_date: date
    taxable_income_clp: Decimal
    deductible_amount_clp: Decimal


@dataclass(frozen=True, slots=True)
class ComputeIncomeTaxResultDTO:
    period_id: int
    tax: IncomeTaxComputation


@dataclass(frozen=True, slots=True)
class IncomeTaxBracketDTO:
    valid_from: date
    valid_to: date | None
    lower_bound_utm: Decimal
    upper_bound_utm: Decimal | None
    marginal_rate: Decimal
    rebate_utm: Decimal


@dataclass(frozen=True, slots=True)
class IncomeTaxBracketWriteDTO:
    valid_from: date
    valid_to: date | None
    lower_bound_utm: Decimal
    upper_bound_utm: Decimal | None
    marginal_rate: Decimal
    rebate_utm: Decimal


@dataclass(frozen=True, slots=True)
class RefreshIncomeTaxBracketsCommandDTO:
    year: int


@dataclass(frozen=True, slots=True)
class RefreshIncomeTaxBracketsResultDTO:
    year: int
    refreshed_months: int
    upserted_brackets: int


@dataclass(frozen=True, slots=True)
class DeflateAmountsCommandDTO:
    period_id: int
    target_year: int
    target_month: int
    index_code: str = "IPC_CL"


@dataclass(frozen=True, slots=True)
class DeflatedAmountDTO:
    nominal_clp: Decimal
    real_clp: Decimal


@dataclass(frozen=True, slots=True)
class DeflateAmountsResultDTO:
    period_id: int
    index_code: str
    source_year: int
    source_month: int
    target_year: int
    target_month: int
    source_index_value: Decimal
    target_index_value: Decimal
    taxable_income: DeflatedAmountDTO
    gross_income: DeflatedAmountDTO
    total_discounts: DeflatedAmountDTO
    net_pay: DeflatedAmountDTO
