"""Domain value objects."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Money:
    """Represent Money."""

    amount: Decimal
    currency: str = "CLP"
