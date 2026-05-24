"""Contribution-related domain models."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class HealthInstitutionKind(StrEnum):
    """Represent Health Institution Kind."""

    FONASA = "fonasa"
    ISAPRE = "isapre"


class EmploymentContractKind(StrEnum):
    """Represent Employment Contract Kind."""

    INDEFINITE = "indefinite"
    FIXED_TERM = "fixed_term"


@dataclass(frozen=True, slots=True)
class PensionInstitution:
    """Represent Pension Institution."""

    code: str
    name: str
    mandatory_rate: Decimal


@dataclass(frozen=True, slots=True)
class HealthInstitution:
    """Represent Health Institution."""

    code: str
    name: str
    kind: HealthInstitutionKind
    mandatory_rate: Decimal


@dataclass(frozen=True, slots=True)
class ContributionCap:
    """Represent Contribution Cap."""

    cap_type: str
    valid_from: date
    valid_to: date | None
    value_uf: Decimal


@dataclass(frozen=True, slots=True)
class PensionPlan:
    """Represent Pension Plan."""

    id: int
    institution: PensionInstitution
    valid_from: date
    valid_to: date | None
    additional_rate: Decimal


@dataclass(frozen=True, slots=True)
class HealthPlan:
    """Represent Health Plan."""

    id: int
    institution: HealthInstitution
    valid_from: date
    valid_to: date | None
    plan_name: str | None
    contracted_uf: Decimal


@dataclass(frozen=True, slots=True)
class PensionContribution:
    """Represent Pension Contribution."""

    institution_code: str
    taxable_clp: Decimal
    cap_clp: Decimal
    capped_base_clp: Decimal
    base_amount_clp: Decimal
    additional_amount_clp: Decimal


@dataclass(frozen=True, slots=True)
class HealthContribution:
    """Represent Health Contribution."""

    institution_code: str
    institution_kind: HealthInstitutionKind
    taxable_clp: Decimal
    cap_clp: Decimal
    capped_base_clp: Decimal
    base_amount_clp: Decimal
    contracted_uf: Decimal
    contracted_clp: Decimal
    additional_amount_clp: Decimal


@dataclass(frozen=True, slots=True)
class UnemploymentContribution:
    """Represent Unemployment Contribution."""

    contract_kind: EmploymentContractKind
    taxable_clp: Decimal
    cap_clp: Decimal
    capped_base_clp: Decimal
    employee_rate: Decimal
    employee_amount_clp: Decimal
    employer_rate: Decimal
    employer_amount_clp: Decimal
