"""Contribution-related domain models."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class HealthInstitutionKind(StrEnum):
    FONASA = "fonasa"
    ISAPRE = "isapre"


@dataclass(frozen=True, slots=True)
class ContributionCap:
    cap_type: str
    valid_from: date
    valid_to: date | None
    value_uf: Decimal
