"""Use case for synchronizing official monthly income tax brackets."""

from dataclasses import dataclass

from payroll.application.errors import PayrollDependencyError
from payroll.application.dto import RefreshIncomeTaxBracketsCommandDTO, RefreshIncomeTaxBracketsResultDTO
from payroll.application.ports.rate_provider import IncomeTaxBracketProvider
from payroll.application.ports.repositories import ReferenceDataRepository


@dataclass(slots=True)
class RefreshIncomeTaxBrackets:
    """Represent Refresh Income Tax Brackets."""

    repository: ReferenceDataRepository
    provider: IncomeTaxBracketProvider

    async def execute(
        self,
        command: RefreshIncomeTaxBracketsCommandDTO,
    ) -> RefreshIncomeTaxBracketsResultDTO:
        """Handle execute."""
        brackets = await self.provider.fetch_income_tax_brackets(command.year)
        if not brackets:
            raise PayrollDependencyError(f"No official income tax brackets were found for {command.year}.")

        upserted_brackets = await self.repository.upsert_income_tax_brackets(brackets)
        refreshed_months = len({item.valid_from for item in brackets})
        return RefreshIncomeTaxBracketsResultDTO(
            year=command.year,
            refreshed_months=refreshed_months,
            upserted_brackets=upserted_brackets,
        )
