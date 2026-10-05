"""Shared employer and contract test commands."""

from datetime import date

from payroll.application.dto import (
    EmployerMaintenanceDTO,
    EmploymentContractMaintenanceDTO,
)


def employer_command(id: int | None = None) -> EmployerMaintenanceDTO:
    """Build an employer command."""
    return EmployerMaintenanceDTO(
        id=id,
        name="ACME",
        tax_id=None,
        country_code="CL",
        payment_date_rule="last_business_day_of_month",
        payment_fixed_day_roll="previous_business_day",
    )


def contract_command(id: int | None = None) -> EmploymentContractMaintenanceDTO:
    """Build a contract command."""
    return EmploymentContractMaintenanceDTO(
        id=id,
        employer_id=1,
        started_at=date(2026, 1, 1),
        ended_at=None,
        is_indefinite=True,
        position="Engineer",
    )
