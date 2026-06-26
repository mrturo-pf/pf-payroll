"""Port definition for income tax bracket lookup."""

from datetime import date
from decimal import Decimal
from typing import Protocol

from payroll.domain.taxes import IncomeTaxBracket


class IncomeTaxBracketPort(Protocol):
    """Lookup port for income tax brackets."""

    async def get_income_tax_bracket(
        self, payment_date: date, taxable_base_utm: Decimal
    ) -> IncomeTaxBracket | None:
        """Return the matching income tax bracket, or None if not found."""
        ...
