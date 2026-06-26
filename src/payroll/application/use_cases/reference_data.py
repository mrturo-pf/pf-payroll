"""Read-only reference-data queries."""

from dataclasses import dataclass

from payroll.application.dto import (
    ContributionCapDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    PayrollConceptDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
)
from payroll.application.ports.repositories import ReferenceDataRepository


@dataclass(slots=True)
class ReferenceDataQueries:
    """Provide reference data queries."""

    repository: ReferenceDataRepository

    async def list_pension_institutions(self) -> list[PensionInstitutionDTO]:
        """List pension institutions."""
        return await self.repository.list_pension_institutions()

    async def list_health_institutions(
        self, *, include_inactive: bool = False
    ) -> list[HealthInstitutionDTO]:
        """List health institutions."""
        return await self.repository.list_health_institutions(
            include_inactive=include_inactive
        )

    async def list_pension_plans(self) -> list[PensionPlanDTO]:
        """List pension plans."""
        return await self.repository.list_pension_plans()

    async def list_health_plans(
        self, *, include_inactive: bool = False
    ) -> list[HealthPlanDTO]:
        """List health plans."""
        return await self.repository.list_health_plans(
            include_inactive=include_inactive
        )

    async def list_contribution_caps(self) -> list[ContributionCapDTO]:
        """List contribution caps."""
        return await self.repository.list_contribution_caps()

    async def list_payroll_concepts(self) -> list[PayrollConceptDTO]:
        """List payroll concepts."""
        return await self.repository.list_payroll_concepts()
