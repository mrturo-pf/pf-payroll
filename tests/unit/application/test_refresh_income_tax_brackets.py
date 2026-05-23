from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    IncomeTaxBracketWriteDTO,
    RefreshIncomeTaxBracketsCommandDTO,
    RefreshIncomeTaxBracketsResultDTO,
)
from payroll.application.use_cases.refresh_income_tax_brackets import RefreshIncomeTaxBrackets


class StubReferenceDataRepository:
    def __init__(self) -> None:
        self.brackets: list[IncomeTaxBracketWriteDTO] | None = None

    async def upsert_income_tax_brackets(self, brackets: list[IncomeTaxBracketWriteDTO]) -> int:
        self.brackets = brackets
        return len(brackets)


class StubIncomeTaxBracketProvider:
    def __init__(self, brackets: list[IncomeTaxBracketWriteDTO]) -> None:
        self.brackets = brackets
        self.requested_year: int | None = None

    async def fetch_income_tax_brackets(self, year: int) -> list[IncomeTaxBracketWriteDTO]:
        self.requested_year = year
        return self.brackets


@pytest.mark.asyncio
async def test_refresh_income_tax_brackets_fetches_official_rows_and_persists_them() -> None:
    brackets = [
        IncomeTaxBracketWriteDTO(
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 1, 31),
            lower_bound_utm=Decimal("0.0000"),
            upper_bound_utm=Decimal("13.5000"),
            marginal_rate=Decimal("0"),
            rebate_utm=Decimal("0.0000"),
        ),
        IncomeTaxBracketWriteDTO(
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 1, 31),
            lower_bound_utm=Decimal("13.5000"),
            upper_bound_utm=Decimal("30.0000"),
            marginal_rate=Decimal("0.04"),
            rebate_utm=Decimal("0.5400"),
        ),
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
async def test_refresh_income_tax_brackets_raises_when_provider_returns_no_official_rows() -> None:
    with pytest.raises(ValueError, match="No official income tax brackets were found for 2026."):
        await RefreshIncomeTaxBrackets(
            StubReferenceDataRepository(),
            StubIncomeTaxBracketProvider([]),
        ).execute(RefreshIncomeTaxBracketsCommandDTO(year=2026))
