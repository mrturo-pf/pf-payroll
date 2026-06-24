"""Tests for test refresh income tax brackets."""

from datetime import date
from decimal import Decimal

import pytest

from helpers.reference_data import sample_jan_2026_income_tax_brackets
from payroll.application.dto import (
    IncomeTaxBracketWriteDTO,
    RefreshIncomeTaxBracketsCommandDTO,
    RefreshIncomeTaxBracketsResultDTO,
)
from payroll.application.use_cases.refresh_income_tax_brackets import (
    RefreshIncomeTaxBrackets,
)


class StubReferenceDataRepository:
    """Test double for Reference Data Repository."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.brackets: list[IncomeTaxBracketWriteDTO] | None = None

    async def upsert_income_tax_brackets(
        self, brackets: list[IncomeTaxBracketWriteDTO]
    ) -> int:
        """Handle upsert income tax brackets."""
        self.brackets = brackets
        return len(brackets)


class StubIncomeTaxBracketProvider:
    """Test double for Income Tax Bracket Provider."""

    def __init__(self, brackets: list[IncomeTaxBracketWriteDTO]) -> None:
        """Initialize the instance."""
        self.brackets = brackets
        self.requested_year: int | None = None

    async def fetch_income_tax_brackets(
        self, year: int
    ) -> list[IncomeTaxBracketWriteDTO]:
        """Handle fetch income tax brackets."""
        self.requested_year = year
        return self.brackets


@pytest.mark.asyncio
async def test_refresh_income_tax_brackets_fetches_and_persists_rows() -> None:
    """Test fetching and persisting official income tax brackets."""
    brackets = [
        *sample_jan_2026_income_tax_brackets(),
        IncomeTaxBracketWriteDTO(
            valid_from=date(2026, 2, 1),
            valid_to=date(2026, 2, 28),
            lower_bound_utm=Decimal("0.0000"),
            upper_bound_utm=Decimal("13.5000"),
            marginal_rate=Decimal("0"),
            rebate_utm=Decimal("0.0000"),
        ),
    ]
    repository = StubReferenceDataRepository()
    provider = StubIncomeTaxBracketProvider(brackets)

    result = await RefreshIncomeTaxBrackets(repository, provider).execute(
        RefreshIncomeTaxBracketsCommandDTO(year=2026)
    )

    assert provider.requested_year == 2026
    assert repository.brackets == brackets
    assert result == RefreshIncomeTaxBracketsResultDTO(
        year=2026,
        refreshed_months=2,
        upserted_brackets=3,
    )


@pytest.mark.asyncio
async def test_refresh_income_tax_brackets_rejects_empty_provider_response() -> None:
    """Test rejection when the provider returns no official income tax rows."""
    with pytest.raises(
        ValueError, match="No official income tax brackets were found for 2026."
    ):
        await RefreshIncomeTaxBrackets(
            StubReferenceDataRepository(),
            StubIncomeTaxBracketProvider([]),
        ).execute(RefreshIncomeTaxBracketsCommandDTO(year=2026))
