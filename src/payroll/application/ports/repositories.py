"""Port definitions for repositories."""

from typing import Protocol

from payroll.application.dto import (
    ContributionCapDTO,
    CurrencyDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    PayrollConceptDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
)


class ReferenceDataRepository(Protocol):
    """Read-only access to reference catalogs."""

    async def list_currencies(self) -> list[CurrencyDTO]: ...

    async def list_pension_institutions(self) -> list[PensionInstitutionDTO]: ...

    async def list_health_institutions(self) -> list[HealthInstitutionDTO]: ...

    async def list_pension_plans(self) -> list[PensionPlanDTO]: ...

    async def list_health_plans(self) -> list[HealthPlanDTO]: ...

    async def list_contribution_caps(self) -> list[ContributionCapDTO]: ...

    async def list_payroll_concepts(self) -> list[PayrollConceptDTO]: ...


class PayrollRepository(Protocol):
    """Placeholder port for payroll persistence."""
