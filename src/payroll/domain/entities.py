"""Core domain entities."""

from dataclasses import dataclass
from datetime import date


@dataclass(slots=True)
class PayrollPeriod:
    """Represent Payroll Period."""

    employer_id: int
    period_year: int
    period_month: int
    payment_date: date
    worked_days: int = 30
