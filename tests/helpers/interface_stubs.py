"""Shared interface stub helpers for unit tests."""

from datetime import date
from decimal import Decimal

from payroll.application.dto import HealthPlanDTO, PensionPlanDTO
from payroll.domain.contributions import HealthInstitutionKind


def sample_pension_plan() -> PensionPlanDTO:
    """Return a sample pension plan for testing."""
    return PensionPlanDTO(
        id=1,
        institution_code="AFP_UNO",
        institution_name="AFP Uno",
        valid_from=date(2026, 1, 1),
        valid_to=None,
        additional_rate=Decimal("0.0127"),
    )


def sample_health_plan() -> HealthPlanDTO:
    """Return a sample health plan for testing."""
    return HealthPlanDTO(
        id=2,
        institution_code="FONASA",
        institution_name="Fonasa",
        institution_kind=HealthInstitutionKind.FONASA,
        valid_from=date(2026, 1, 1),
        valid_to=None,
        plan_name="Base",
        contracted_uf=Decimal("0"),
    )
