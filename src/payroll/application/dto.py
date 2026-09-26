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
class PayrollPeriodRangeFields:
    """Share the common payroll period range fields."""

    period_year: int
    period_month: int
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class PayrollPeriodDetailFields:
    """Share the common payroll period detail fields."""

    id: int
    employer_id: int
    employer_name: str
    employer_tax_id: str | None
    employer_country_code: str
    employer_started_at: date
    employer_ended_at: date | None
    period_year: int
    period_month: int
    payment_date: date
    worked_days: int


@dataclass(frozen=True, slots=True)
class MoneyDTO:
    """Represent Money DTO."""

    amount: Decimal
    currency: str = "CLP"


@dataclass(frozen=True, slots=True)
class CurrencyDTO:
    """Represent Currency DTO."""

    code: str
    name: str
    is_fiat: bool
    unit_kind: str


@dataclass(frozen=True, slots=True)
class PensionInstitutionDTO:
    """Represent Pension Institution DTO."""

    code: str
    name: str
    mandatory_rate: Decimal
    is_active: bool


@dataclass(frozen=True, slots=True)
class HealthInstitutionDTO:
    """Represent Health Institution DTO."""

    code: str
    name: str
    kind: HealthInstitutionKind
    mandatory_rate: Decimal
    is_active: bool


@dataclass(frozen=True, slots=True)
class PensionPlanDTO:
    """Represent Pension Plan DTO."""

    id: int
    institution_code: str
    institution_name: str
    valid_from: date
    valid_to: date | None
    additional_rate: Decimal


@dataclass(frozen=True, slots=True)
class HealthPlanDTO:
    """Represent Health Plan DTO."""

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
    """Represent Contribution Cap DTO."""

    cap_type: str
    valid_from: date
    valid_to: date | None
    value_uf: Decimal


@dataclass(frozen=True, slots=True)
class ExchangeRateDTO:
    """Represent Exchange Rate DTO."""

    currency_code: str
    rate_date: date
    value_clp: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class EconomicIndexDTO:
    """Represent Economic Index DTO."""

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
    """Represent Payroll Concept DTO."""

    code: str
    name: str
    kind: PayrollConceptKind
    is_taxable: bool


@dataclass(frozen=True, slots=True)
class ImportPayrollRowDTO:
    """Represent Import Payroll Row DTO."""

    employer: str
    period_year: int
    period_month: int
    payment_date: date
    status: PayrollStatusKind
    employment_contract_kind: EmploymentContractKind
    concept_code: str
    amount_clp: Decimal
    worked_days: int = 30
    declared_net_pay_clp: Decimal | None = None
    expected_net_pay_clp: Decimal | None = None
    net_pay_difference_clp: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PdfImportPreviewRowDTO:
    """Represent a single candidate row extracted from a payroll PDF.

    Unlike ImportPayrollRowDTO, this never persists anything and may carry an
    unresolved concept_code -- confidence and raw_label exist precisely so a
    human can review and correct low-confidence or unmatched rows before
    resubmitting them through the confirm endpoint.
    """

    raw_label: str
    amount_clp: Decimal
    kind: PayrollConceptKind
    concept_code: str | None
    confidence: float


@dataclass(frozen=True, slots=True)
class EmployerPaymentRuleDTO:
    """Represent an employer's configured payment-date rule.

    Mirrors `EmployerModel`'s payment-rule columns and `resolve_payment_date`
    's keyword parameters exactly, so a caller can forward this DTO's fields
    straight into that function without any translation step.
    """

    country_code: str
    payment_date_rule: str
    payment_month_offset: int
    payment_day_of_month: int | None
    payment_business_day_offset: int
    payment_calendar_day_offset: int
    payment_effective_on_processing_next_day: bool
    payment_fixed_day_roll: str


@dataclass(frozen=True, slots=True)
class PdfImportPreviewDTO:
    """Represent the full result of previewing a payroll PDF.

    All header fields are optional because extraction must never fail loudly
    -- a PDF that matches no known template still returns a 200 with mostly
    empty/unresolved fields instead of raising.
    """

    employer: str | None
    period_year: int | None
    period_month: int | None
    payment_date: date | None
    worked_days: int | None
    declared_net_pay_clp: Decimal | None
    employment_contract_kind: EmploymentContractKind | None
    template_id: str | None
    rows: list[PdfImportPreviewRowDTO] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ImportedContributionValidationDTO:
    """Represent imported contribution validation results."""

    declared_pension_base_clp: Decimal | None = None
    expected_pension_base_clp: Decimal | None = None
    pension_base_difference_clp: Decimal | None = None
    declared_pension_additional_clp: Decimal | None = None
    expected_pension_additional_clp: Decimal | None = None
    pension_additional_difference_clp: Decimal | None = None
    declared_health_base_clp: Decimal | None = None
    expected_health_base_clp: Decimal | None = None
    health_base_difference_clp: Decimal | None = None
    declared_health_plan_additional_clp: Decimal | None = None
    expected_health_plan_additional_clp: Decimal | None = None
    health_plan_additional_difference_clp: Decimal | None = None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class ImportedComplementaryInsuranceValidationDTO:
    """Represent imported complementary insurance validation results."""

    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ImportedPayrollPeriodDTO:
    """Represent Imported Payroll Period DTO."""

    id: int
    employer: str
    period_year: int
    period_month: int
    payment_date: date
    status: PayrollStatusKind
    employment_contract_kind: EmploymentContractKind
    item_count: int
    worked_days: int = 30
    declared_net_pay_clp: Decimal | None = None
    expected_net_pay_clp: Decimal | None = None
    net_pay_difference_clp: Decimal | None = None
    net_pay_warning: str | None = None
    contribution_validation: ImportedContributionValidationDTO | None = None
    complementary_insurance_validation: (
        ImportedComplementaryInsuranceValidationDTO | None
    ) = None


@dataclass(frozen=True, slots=True)
class ImportPayrollResultDTO:
    """Represent Import Payroll Result DTO."""

    imported_periods: int
    imported_items: int
    periods: list[ImportedPayrollPeriodDTO]


@dataclass(frozen=True, slots=True)
class ComputeContributionsCommandDTO:
    """Represent Compute Contributions Command DTO."""

    period_id: int
    pension_plan_id: int
    health_plan_id: int
    uf_value_clp: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ComputeUnemploymentInsuranceCommandDTO:
    """Represent Compute Unemployment Insurance Command DTO."""

    period_id: int
    uf_value_clp: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AssignPlansCommandDTO:
    """Represent Assign Plans Command DTO."""

    period_id: int
    pension_plan_id: int
    health_plan_id: int


@dataclass(frozen=True, slots=True)
class AssignPlansResultDTO:
    """Represent Assign Plans Result DTO."""

    period_id: int
    payment_date: date
    pension_plan_id: int
    health_plan_id: int


@dataclass(frozen=True, slots=True)
class ReviewPayrollPeriodCommandDTO:
    """Represent Review Payroll Period Command DTO."""

    period_id: int


@dataclass(frozen=True, slots=True)
class ReviewPayrollPeriodResultDTO:
    """Represent Review Payroll Period Result DTO."""

    period_id: int
    payment_date: date
    status: PayrollStatusKind


@dataclass(frozen=True, slots=True)
class ContributionComputationContextDTO:
    """Represent Contribution Computation Context DTO."""

    period_id: int
    payment_date: date
    taxable_income_clp: Decimal
    employment_contract_kind: EmploymentContractKind
    pension_plan: PensionPlan
    health_plan: HealthPlan
    cap: ContributionCap
    unemployment_cap: ContributionCap


@dataclass(frozen=True, slots=True)
class UnemploymentComputationContextDTO:
    """Represent Unemployment Computation Context DTO."""

    period_id: int
    payment_date: date
    taxable_income_clp: Decimal
    employment_contract_kind: EmploymentContractKind
    unemployment_cap: ContributionCap


@dataclass(frozen=True, slots=True)
class ComputeContributionsResultDTO:
    """Represent Compute Contributions Result DTO."""

    period_id: int
    pension_plan_id: int
    health_plan_id: int
    taxable_income_clp: Decimal
    pension: PensionContribution
    health: HealthContribution
    unemployment: UnemploymentContribution
    total_discount_clp: Decimal


@dataclass(frozen=True, slots=True)
class ComputeUnemploymentInsuranceResultDTO:
    """Represent Compute Unemployment Insurance Result DTO."""

    period_id: int
    unemployment: UnemploymentContribution


@dataclass(frozen=True, slots=True)
class PayrollItemDetailDTO:
    """Represent Payroll Item Detail DTO."""

    concept_code: str
    concept_name: str
    kind: PayrollConceptKind
    is_taxable: bool
    amount_clp: Decimal
    notes: str | None


@dataclass(frozen=True, slots=True)
class PayrollSummaryDTO:
    """Represent Payroll Summary DTO."""

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
    declared_net_pay_clp: Decimal | None = None
    expected_net_pay_clp: Decimal | None = None
    net_pay_difference_clp: Decimal | None = None
    net_pay_warning: str | None = None


@dataclass(frozen=True, slots=True)
class PayrollPeriodDetailDTO(PayrollPeriodDetailFields):
    """Represent Payroll Period Detail DTO."""

    status: PayrollStatusKind
    employment_contract_kind: EmploymentContractKind
    pension_plan_id: int | None
    health_plan_id: int | None
    items: list[PayrollItemDetailDTO]
    summary: PayrollSummaryDTO | None
    health_plan_ids: tuple[int, ...] | None = None
    health_institution_is_active: bool | None = None


@dataclass(frozen=True, slots=True)
class PayrollPeriodRangeDTO(PayrollPeriodRangeFields):
    """Represent a payroll period date range."""

    net_pay_clp: Decimal | None
    is_current: bool
    inferred: bool
    increase: bool | None = None
    salary_base: Decimal | None = None
    worked_days: int | None = None
    is_lookback: bool = False


@dataclass(frozen=True, slots=True)
class GeneratedPayrollReportDTO:
    """Represent Generated Payroll Report DTO."""

    period_id: int
    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ComputeIncomeTaxCommandDTO:
    """Represent Compute Income Tax Command DTO."""

    period_id: int
    utm_value_clp: Decimal | None = None


@dataclass(frozen=True, slots=True)
class IncomeTaxContextDTO:
    """Represent Income Tax Context DTO."""

    period_id: int
    payment_date: date
    taxable_income_clp: Decimal
    deductible_amount_clp: Decimal


@dataclass(frozen=True, slots=True)
class ComputeIncomeTaxResultDTO:
    """Represent Compute Income Tax Result DTO."""

    period_id: int
    tax: IncomeTaxComputation


@dataclass(frozen=True, slots=True)
class IncomeTaxBracketDTO:
    """Represent Income Tax Bracket DTO."""

    valid_from: date
    valid_to: date | None
    lower_bound_utm: Decimal
    upper_bound_utm: Decimal | None
    marginal_rate: Decimal
    rebate_utm: Decimal


@dataclass(frozen=True, slots=True)
class DeflateAmountsCommandDTO:
    """Represent Deflate Amounts Command DTO."""

    period_id: int
    target_year: int
    target_month: int
    index_code: str = "IPC_CL"


@dataclass(frozen=True, slots=True)
class DeflatedAmountDTO:
    """Represent Deflated Amount DTO."""

    nominal_clp: Decimal
    real_clp: Decimal


@dataclass(frozen=True, slots=True)
class DeflateAmountsResultDTO:
    """Represent Deflate Amounts Result DTO."""

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


@dataclass(frozen=True, slots=True)
class ComplementaryInsuranceCostDTO:
    """Represent complementary insurance cost for a plan."""

    plan_id: int
    plan_name: str
    cost_clp: Decimal


@dataclass(frozen=True, slots=True)
class ComputeComplementaryInsuranceCommandDTO:
    """Represent Compute Complementary Insurance Command DTO."""

    period_id: int


@dataclass(frozen=True, slots=True)
class ComplementaryInsuranceValidationAuditDTO:
    """Represent audit trail for complementary insurance validation."""

    period_id: int
    gross_income_clp: Decimal
    taxable_income_clp: Decimal
    total_legal_deductions_clp: Decimal
    declared_employer_contribution_clp: Decimal | None
    calculated_total_cost_clp: Decimal
    individual_plan_costs: list[ComplementaryInsuranceCostDTO] = field(
        default_factory=list
    )
    difference_clp: Decimal = Decimal(0)
    tolerance_clp: Decimal = Decimal(100)
    has_discrepancy: bool = False


@dataclass(frozen=True, slots=True)
class ComputeComplementaryInsuranceResultDTO:
    """Represent Compute Complementary Insurance Result DTO."""

    period_id: int
    costs: list[ComplementaryInsuranceCostDTO]
    total_cost_clp: Decimal
    audit: ComplementaryInsuranceValidationAuditDTO | None = None
