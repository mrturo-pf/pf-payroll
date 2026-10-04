"""Tests for structured JSON import preflight invariants."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import calendar

import pytest

from payroll.application.dto import ImportPayrollRowDTO
from payroll.infrastructure.db.repositories.payroll_repository import (
    SqlAlchemyPayrollRepository,
)


class Result:
    """Minimal async-query result double."""

    def __init__(
        self,
        *,
        scalar_one: object | None = None,
        first: object | None = None,
        scalar_rows: list[object] | None = None,
    ) -> None:
        """Initialize the result."""
        self._scalar_one = scalar_one
        self._first = first
        self._scalar_rows = scalar_rows or []

    def scalar_one_or_none(self) -> object | None:
        """Return a scalar or None."""
        return self._scalar_one

    def first(self) -> object | None:
        """Return the first row."""
        return self._first

    def scalars(self) -> "Result":
        """Return this result as a scalar result."""
        return self

    def all(self) -> list[object]:
        """Return scalar rows."""
        return self._scalar_rows


class Session:
    """Queue query results for preflight tests."""

    def __init__(self, results: list[Result]) -> None:
        """Initialize the session."""
        self._results = results

    async def execute(self, _statement: object) -> Result:
        """Return the next queued result."""
        return self._results.pop(0)


def row(
    *,
    period_id: int | None = None,
    worked_days: int = 10,
    period_month: int = 8,
) -> ImportPayrollRowDTO:
    """Build a JSON-origin row command."""
    return ImportPayrollRowDTO(
        employer="ACME",
        period_year=2026,
        period_month=period_month,
        payment_date=date(
            2026, period_month, calendar.monthrange(2026, period_month)[1]
        ),
        concept_code="SALARY_BASE",
        amount_clp=Decimal("100"),
        worked_days=worked_days,
        period_id=period_id,
        require_period_contract_validation=True,
    )


@pytest.mark.asyncio
async def test_preflight_reports_existing_natural_key() -> None:
    """Report an existing no-ID natural key with its persisted ID."""
    employer = SimpleNamespace(id=7)
    existing = SimpleNamespace(id=481, worked_days=10)
    repository = SqlAlchemyPayrollRepository(
        Session(
            [
                Result(scalar_one=employer),
                Result(scalar_one=existing),
                Result(first=object()),
            ]
        )  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="natural keys already exist"):
        await repository._preflight_json_periods({("ACME", 2026, 8): [row()]})


@pytest.mark.asyncio
async def test_preflight_reports_missing_contract_interval() -> None:
    """Reject an insertion without a contract overlapping the worked month."""
    employer = SimpleNamespace(id=7)
    repository = SqlAlchemyPayrollRepository(
        Session(
            [Result(scalar_one=employer), Result(scalar_one=None), Result(first=None)]
        )  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="eligible contract interval"):
        await repository._preflight_json_periods({("ACME", 2026, 8): [row()]})


@pytest.mark.asyncio
async def test_preflight_reports_worked_days_capacity() -> None:
    """Reject a projected month total above 30 days."""
    employer = SimpleNamespace(id=7)
    existing = SimpleNamespace(id=481, worked_days=25)
    repository = SqlAlchemyPayrollRepository(
        Session(
            [
                Result(scalar_one=employer),
                Result(scalar_one=None),
                Result(first=object()),
                Result(),
                Result(scalar_rows=[existing]),
            ]
        )  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="worked-days capacity"):
        await repository._preflight_json_periods({("ACME", 2026, 8): [row()]})


@pytest.mark.asyncio
async def test_preflight_allows_explicit_update_with_ended_contract() -> None:
    """Allow an explicit historical update without rechecking insertion eligibility."""
    existing = SimpleNamespace(id=481, worked_days=20)
    repository = SqlAlchemyPayrollRepository(
        Session(
            [
                Result(scalar_one=SimpleNamespace(id=7)),
                Result(),
                Result(scalar_rows=[existing]),
            ]
        )  # type: ignore[arg-type]
    )

    await repository._preflight_json_periods(
        {("ACME", 2026, 8): [row(period_id=481, worked_days=25)]}
    )


@pytest.mark.asyncio
async def test_preflight_skips_legacy_rows() -> None:
    """Ignore legacy rows that do not carry the JSON-origin marker."""
    legacy = ImportPayrollRowDTO(
        employer="LEGACY",
        period_year=2026,
        period_month=8,
        payment_date=date(2026, 8, 31),
        concept_code="SALARY_BASE",
        amount_clp=Decimal("100"),
    )
    repository = SqlAlchemyPayrollRepository(
        Session([Result(), Result(scalar_rows=[])])  # type: ignore[arg-type]
    )

    await repository._preflight_json_periods({("LEGACY", 2026, 8): [legacy]})


@pytest.mark.asyncio
async def test_preflight_checks_multiple_period_months() -> None:
    """Check projected capacity independently for two calendar months."""
    repository = SqlAlchemyPayrollRepository(
        Session(
            [
                Result(scalar_one=SimpleNamespace(id=7)),
                Result(scalar_one=None),
                Result(first=object()),
                Result(scalar_one=SimpleNamespace(id=8)),
                Result(scalar_one=None),
                Result(first=object()),
                Result(),
                Result(scalar_rows=[]),
                Result(),
                Result(scalar_rows=[]),
            ]
        )  # type: ignore[arg-type]
    )

    await repository._preflight_json_periods(
        {
            ("ACME", 2026, 8): [row()],
            ("ACME", 2026, 9): [row(period_month=9)],
        }
    )


@pytest.mark.asyncio
async def test_preflight_rejects_missing_employer() -> None:
    """Reject JSON insertion when the employer does not exist."""
    repository = SqlAlchemyPayrollRepository(
        Session([Result(scalar_one=None)])  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="employment contract must exist"):
        await repository._preflight_json_periods({("ACME", 2026, 8): [row()]})


@pytest.mark.asyncio
async def test_import_rows_invokes_json_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invoke preflight when a row carries the JSON-origin marker."""
    repository = SqlAlchemyPayrollRepository(
        Session([Result(scalar_rows=[SimpleNamespace(id=1, code="SALARY_BASE")])])  # type: ignore[arg-type]
    )

    async def fail_preflight(_groups: object) -> None:
        """Stop after proving the preflight dispatch."""
        raise ValueError("preflight called")

    monkeypatch.setattr(repository, "_preflight_json_periods", fail_preflight)

    with pytest.raises(ValueError, match="preflight called"):
        await repository.import_rows([row()])
