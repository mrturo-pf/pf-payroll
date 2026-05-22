"""Application DTOs."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from payroll.domain.contributions import (
    ContributionCap,
    HealthContribution,
    HealthInstitutionKind,
    HealthPlan,
    PensionContribution,
    PensionPlan,
)

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
    uf_value_clp: Decimal


@dataclass(frozen=True, slots=True)
class ContributionComputationContextDTO:
    period_id: int
    payment_date: date
    taxable_income_clp: Decimal
    pension_plan: PensionPlan
    health_plan: HealthPlan
    cap: ContributionCap


@dataclass(frozen=True, slots=True)
class ComputeContributionsResultDTO:
    period_id: int
    pension_plan_id: int
    health_plan_id: int
    taxable_income_clp: Decimal
    pension: PensionContribution
    health: HealthContribution
    total_discount_clp: Decimal
