"""Contribution-related domain models."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class HealthInstitutionKind(StrEnum):
    FONASA = "fonasa"
    ISAPRE = "isapre"


@dataclass(frozen=True, slots=True)
class PensionInstitution:
    code: str
    name: str
    mandatory_rate: Decimal


@dataclass(frozen=True, slots=True)
class HealthInstitution:
    code: str
    name: str
    kind: HealthInstitutionKind
    mandatory_rate: Decimal


@dataclass(frozen=True, slots=True)
class ContributionCap:
    cap_type: str
    valid_from: date
    valid_to: date | None
    value_uf: Decimal


@dataclass(frozen=True, slots=True)
class PensionPlan:
    id: int
    institution: PensionInstitution
    valid_from: date
    valid_to: date | None
    additional_rate: Decimal


@dataclass(frozen=True, slots=True)
class HealthPlan:
    id: int
    institution: HealthInstitution
    valid_from: date
    valid_to: date | None
    plan_name: str | None
    contracted_uf: Decimal


@dataclass(frozen=True, slots=True)
class PensionContribution:
    institution_code: str
    taxable_clp: Decimal
    cap_clp: Decimal
    capped_base_clp: Decimal
    base_amount_clp: Decimal
    additional_amount_clp: Decimal


@dataclass(frozen=True, slots=True)
class HealthContribution:
    institution_code: str
    institution_kind: HealthInstitutionKind
    taxable_clp: Decimal
    cap_clp: Decimal
    capped_base_clp: Decimal
    base_amount_clp: Decimal
    contracted_uf: Decimal
    contracted_clp: Decimal
    additional_amount_clp: Decimal
