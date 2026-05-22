"""Application DTOs."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MoneyDTO:
    amount: Decimal
    currency: str = "CLP"
