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
from payroll.shared.constants import RECONCILIATION_TOLERANCE_CLP

PayrollConceptKind = Literal["income", "discount"]


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
    period_year: int
    period_month: int
    payment_date: date
    worked_days: int


@dataclass(frozen=True, slots=True)
class EmployerMaintenanceDTO:
    """Represent one employer create/update command and result."""

    id: int | None
    name: str
    tax_id: str | None
    country_code: str
    payment_date_rule: str | None = None
    payment_month_offset: int = 0
    payment_day_of_month: int | None = None
    payment_business_day_offset: int = 0
    payment_calendar_day_offset: int = 0
    payment_effective_on_processing_next_day: bool = False
    payment_fixed_day_roll: str | None = None


@dataclass(frozen=True, slots=True)
class EmploymentContractMaintenanceDTO:
    """Represent one employment-contract create/update command and result."""

    id: int | None
    employer_id: int
    started_at: date
    ended_at: date | None
    is_indefinite: bool
    position: str | None


@dataclass(frozen=True, slots=True)
class EmployerReferenceDTO:
    """Represent the employer reference nested in a contract response."""

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class EmploymentContractReadDTO:
    """Represent an employment contract in a nested read response."""

    id: int
    employer: EmployerReferenceDTO
    started_at: date
    ended_at: date | None
    is_indefinite: bool
    position: str | None
    is_in_effect: bool


@dataclass(frozen=True, slots=True)
class EmploymentContractNestedReadDTO:
    """Represent a contract nested under an employer response."""

    id: int
    started_at: date
    ended_at: date | None
    is_indefinite: bool
    position: str | None
    is_in_effect: bool


@dataclass(frozen=True, slots=True)
class FirstIncreasePeriodReadDTO:
    """Represent the first salary-increase period."""

    year: int
    month: int


@dataclass(frozen=True, slots=True)
class PaymentDateReadDTO:
    """Represent employer payment-date configuration."""

    rule: str | None
    month_offset: int
    day_of_month: int | None
    business_day_offset: int
    calendar_day_offset: int
    effective_on_processing_next_day: bool
    fixed_day_roll: str | None


@dataclass(frozen=True, slots=True)
class IncreaseReadDTO:
    """Represent employer salary-increase configuration."""

    frequency: int | None
    first_increase_period: FirstIncreasePeriodReadDTO | None


@dataclass(frozen=True, slots=True)
class EmployerReadDTO:
    """Represent an employer with its full fields and nested contracts."""

    id: int
    name: str
    tax_id: str | None
    country_code: str
    increase: IncreaseReadDTO
    payment_date: PaymentDateReadDTO
    contracts: list[EmploymentContractNestedReadDTO]


@dataclass(frozen=True, slots=True)
class EmployerMaintenanceResultDTO:
    """Represent an employer maintenance batch result."""

    employers: list[EmployerMaintenanceDTO]


@dataclass(frozen=True, slots=True)
class ContractMaintenanceResultDTO:
    """Represent a contract maintenance batch result."""

    contracts: list[EmploymentContractMaintenanceDTO]


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
    concept_code: str
    amount_clp: Decimal
    worked_days: int = 30
    declared_net_pay_clp: Decimal | None = None
    expected_net_pay_clp: Decimal | None = None
    net_pay_difference_clp: Decimal | None = None
    period_id: int | None = None
    require_period_contract_validation: bool = False


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
class EmploymentContractDTO:
    """Represent the contract effective for an employer and payment date."""

    id: int
    employer_id: int
    started_at: date
    ended_at: date | None
    is_indefinite: bool
    position: str | None


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
    template_id: str | None
    rows: list[PdfImportPreviewRowDTO] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConceptRef:
    """Minimal PAY_CONCEPT projection needed to resolve a template field.

    Returned by `TemplateRepository.resolve_concepts()`, keyed by
    `concept_code` -- carries exactly what a write needs (`id`, to store in
    `PAY_PDF_TEMPLATE_FIELD.concept_id`) and what a client-facing response
    needs (`kind`, since `PdfTemplateFieldDTO`/the API never store it, only
    ever resolve it fresh from `PAY_CONCEPT`). The one DTO allowed to cross
    the `TemplateRepository` port boundary for this lookup -- see pf-db
    migration 0011 for why the storage column is `concept_id`, not
    `concept_code`.
    """

    id: int
    kind: PayrollConceptKind


@dataclass(frozen=True, slots=True)
class PdfTemplateFieldDTO:
    """Represent one label-to-concept mapping rule within a PDF template.

    Plain, JSON-serializable data -- no compiled `re.Pattern` -- this is the
    only shape allowed to cross the application/infrastructure boundary for
    templates; `infrastructure/pdf_import/templates.py`'s `TemplateField`
    (compiled regex) is built *from* this, never the other way around.
    """

    id: int | None
    pdf_label_pattern: str
    concept_code: str
    kind: PayrollConceptKind
    confidence: float = 0.9


@dataclass(frozen=True, slots=True)
class PdfTemplateDTO:
    """Represent a full payroll PDF template, fields included.

    `id`/field `id`s are `None` for a not-yet-persisted template (a create
    request); always populated for anything read back from storage.
    `employer_id` is required -- a template may only be created for an
    employer that already has a `PAY_EMPLOYER` row (i.e. after its first
    payroll import has run; there is no standalone endpoint to create one
    ahead of that). It is never read by `select_template()`/`match_field()`,
    which keep matching purely against `employer_match_pattern` (a regex over
    the raw PDF text), unchanged. `employer_name` follows the same
    None-only-on-write precedent as `id`: always `None` on a create/update
    request (there is nothing to supply -- the repository never stores a copy
    of `PAY_EMPLOYER.name`, see pf-db migration 0012); always a resolved,
    non-None string for anything read back from storage.
    """

    id: int | None
    template_id: str
    employer_id: int
    employer_name: str | None
    employer_match_pattern: str
    version: int
    is_active: bool
    fields: list[PdfTemplateFieldDTO] = field(default_factory=list)


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
class ContributionComputationContextDTO:
    """Represent Contribution Computation Context DTO.

    `health_plan` is the single requested/representative plan (used only for
    its `id`, echoed back on ComputeContributionsResultDTO.health_plan_id).
    `health_plans` is every health plan actually assigned to this period --
    the full, unaggregated list, each with its own valid_from/valid_to --
    used by ContributionCalculator.health() to prorate a mid-month plan
    change day by day instead of an all-or-nothing sum (see
    domain/health_plan_proration.py). period_year/period_month identify
    which calendar month to prorate against; not necessarily payment_date's
    own month for every employer payment-date convention.
    """

    period_id: int
    payment_date: date
    period_year: int
    period_month: int
    taxable_income_clp: Decimal
    pension_plan: PensionPlan
    health_plan: HealthPlan
    health_plans: list[HealthPlan]
    cap: ContributionCap
    unemployment_cap: ContributionCap
    employment_contract_kind: EmploymentContractKind = EmploymentContractKind.INDEFINITE


@dataclass(frozen=True, slots=True)
class UnemploymentComputationContextDTO:
    """Represent Unemployment Computation Context DTO."""

    period_id: int
    payment_date: date
    taxable_income_clp: Decimal
    unemployment_cap: ContributionCap
    employment_contract_kind: EmploymentContractKind = EmploymentContractKind.INDEFINITE


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
    increase: Decimal | None = None
    salary_base: Decimal | None = None
    worked_days: int | None = None
    is_lookback: bool = False
    net_pay_usd: Decimal | None = None
    net_pay_eur: Decimal | None = None
    net_pay_uf: Decimal | None = None
    fixed_uf_clp: Decimal = Decimal("0")
    period_id: int | None = None
    employer_id: int | None = None
    employer_name: str | None = None
    gross_income_clp: Decimal | None = None
    taxable_income_clp: Decimal | None = None
    total_discounts_clp: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PayrollPeriodRangeContextDTO:
    """Bundle a single real period with the context needed to describe it.

    Backs GET /payroll/{period_id}: unlike list_period_ranges() (anchored to
    a fixed window around "today"), this works for *any* period_id
    regardless of age. `predecessor` and `current` are never part of the
    response themselves -- they only supply the salary_base/net_pay_clp
    history `target`'s own `increase`/`net_pay_clp_today` are derived from,
    same two helpers list_period_ranges() already uses per-item.
    """

    target: PayrollPeriodRangeDTO
    predecessor: PayrollPeriodRangeDTO | None
    current: PayrollPeriodRangeDTO | None


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
    tolerance_clp: Decimal = RECONCILIATION_TOLERANCE_CLP
    has_discrepancy: bool = False


@dataclass(frozen=True, slots=True)
class ExportPayrollFiltersDTO:
    """Represent Export Payroll Filters DTO.

    All optional; omitting every field means "export every persisted
    period", mirroring GET /payroll's own no-filter "return
    everything" behavior. Only fields with a concrete precedent as an
    existing filter elsewhere in this API are included here (YAGNI).
    """

    employer: str | None = None
    period_year: int | None = None
    period_month: int | None = None


@dataclass(frozen=True, slots=True)
class ComputeComplementaryInsuranceResultDTO:
    """Represent Compute Complementary Insurance Result DTO."""

    period_id: int
    costs: list[ComplementaryInsuranceCostDTO]
    total_cost_clp: Decimal
    audit: ComplementaryInsuranceValidationAuditDTO | None = None
