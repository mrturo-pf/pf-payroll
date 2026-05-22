"""Read-only reference-data queries."""

from dataclasses import dataclass

from payroll.application.dto import (
    ContributionCapDTO,
    CurrencyDTO,
    HealthInstitutionDTO,
    HealthPlanDTO,
    IncomeTaxBracketDTO,
    PayrollConceptDTO,
    PensionInstitutionDTO,
    PensionPlanDTO,
)
from payroll.application.ports.repositories import ReferenceDataRepository


@dataclass(slots=True)
class ReferenceDataQueries:
    repository: ReferenceDataRepository

    async def list_currencies(self) -> list[CurrencyDTO]:
        return await self.repository.list_currencies()

    async def list_pension_institutions(self) -> list[PensionInstitutionDTO]:
        return await self.repository.list_pension_institutions()

    async def list_health_institutions(self) -> list[HealthInstitutionDTO]:
        return await self.repository.list_health_institutions()

    async def list_pension_plans(self) -> list[PensionPlanDTO]:
        return await self.repository.list_pension_plans()

    async def list_health_plans(self) -> list[HealthPlanDTO]:
        return await self.repository.list_health_plans()

    async def list_contribution_caps(self) -> list[ContributionCapDTO]:
        return await self.repository.list_contribution_caps()

    async def list_payroll_concepts(self) -> list[PayrollConceptDTO]:
        return await self.repository.list_payroll_concepts()

    async def list_income_tax_brackets(self) -> list[IncomeTaxBracketDTO]:
        return await self.repository.list_income_tax_brackets()
