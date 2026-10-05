"""Tests for employer and contract maintenance persistence."""

from datetime import date

import pytest

from unit.employer_contract_fixtures import contract_command, employer_command
from payroll.application import errors
from payroll.application.dto import EmploymentContractMaintenanceDTO
from payroll.infrastructure.db import models
from payroll.infrastructure.db.models import payroll
from payroll.infrastructure.db.repositories.payroll_repository import (
    SqlAlchemyPayrollRepository as Repo,
)


class Result:
    """Small SQLAlchemy result double."""

    def __init__(self, scalar_one=None, scalar_rows=None) -> None:
        """Initialize the result."""
        self.scalar_one_value = scalar_one
        self.rows = scalar_rows or []

    def scalar_one_or_none(self):
        """Return one scalar."""
        return self.scalar_one_value

    def scalars(self):
        """Return scalar result."""
        return self

    def all(self):
        """Return rows."""
        return self.rows


class Session:
    """Queue SQL results and record writes."""

    def __init__(self, results: list[Result]) -> None:
        """Initialize the session."""
        self.results = results
        self.added: list[object] = []
        self.statements: list[object] = []

    async def execute(self, statement: object) -> Result:
        """Return the next result."""
        self.statements.append(statement)
        return self.results.pop(0) if self.results else Result()

    def add(self, model: object) -> None:
        """Record a model."""
        self.added.append(model)
        if getattr(model, "id", None) is None:
            model.id = 900 + len(self.added)  # type: ignore[attr-defined]

    async def flush(self) -> None:
        """Accept a flush."""


def employer_model(id: int = 1) -> models.EmployerModel:
    """Build a fully populated employer model."""
    model = models.EmployerModel(id=id, name="ACME", country_code="CL", tax_id=None)
    model.payment_date_rule = payroll.EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH
    model.payment_fixed_day_roll = payroll.EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY
    return model


@pytest.mark.asyncio
async def test_maintain_employers_creates_and_updates() -> None:
    """Create and update employers in one batch."""
    existing = employer_model(1)
    session = Session([Result(scalar_one=existing)])
    result = await Repo(session).maintain_employers(
        [employer_command(), employer_command(1)]
    )
    assert len(result.employers) == 2
    assert len(session.added) == 1


@pytest.mark.asyncio
async def test_maintain_employers_rejects_unknown_update() -> None:
    """Reject an update targeting an unknown employer."""
    session = Session([Result(scalar_one=None)])
    with pytest.raises(errors.PayrollNotFoundError):
        await Repo(session).maintain_employers([employer_command(9)])


@pytest.mark.asyncio
async def test_maintain_contracts_creates_and_updates() -> None:
    """Create and update contracts after employer validation."""
    existing = payroll.EmploymentContractModel(
        id=4,
        employer_id=1,
        started_at=date(2025, 1, 1),
        ended_at=None,
        is_indefinite=True,
        position="Old",
    )
    session = Session(
        [
            Result(scalar_one=employer_model()),
            Result(scalar_rows=[]),
            Result(scalar_one=employer_model()),
            Result(scalar_rows=[]),
            Result(scalar_one=existing),
        ]
    )
    result = await Repo(session).maintain_contracts(
        [contract_command(), contract_command(4)]
    )
    assert len(result.contracts) == 2
    with pytest.raises(errors.PayrollNotFoundError):
        await Repo(Session([Result(scalar_one=None)])).maintain_contracts(
            [contract_command(99)]
        )


@pytest.mark.asyncio
async def test_maintain_contracts_rejects_unknown_update() -> None:
    """Reject a contract update targeting an unknown contract."""
    session = Session(
        [
            Result(scalar_one=employer_model()),
            Result(scalar_rows=[]),
            Result(scalar_one=None),
        ]
    )
    with pytest.raises(errors.PayrollNotFoundError):
        await Repo(session).maintain_contracts([contract_command(99)])
    """Reject invalid interval and overlapping contracts."""
    bad = EmploymentContractMaintenanceDTO(
        id=None,
        employer_id=1,
        started_at=date(2026, 1, 1),
        ended_at=None,
        is_indefinite=False,
        position="Engineer",
    )
    with pytest.raises(errors.PayrollValidationError):
        await Repo(Session([])).maintain_contracts([bad])
    conflict = payroll.EmploymentContractModel(
        id=8,
        employer_id=2,
        started_at=date(2026, 1, 1),
        ended_at=None,
        is_indefinite=True,
        position=None,
    )
    session = Session(
        [Result(scalar_one=employer_model()), Result(scalar_rows=[conflict])]
    )
    with pytest.raises(errors.PayrollConflictError):
        await Repo(session).maintain_contracts([contract_command()])


@pytest.mark.asyncio
async def test_list_employers_and_contracts() -> None:
    """List employers and contracts."""
    model = employer_model()
    contract = payroll.EmploymentContractModel(
        id=1,
        employer_id=1,
        started_at=date(2026, 1, 1),
        ended_at=None,
        is_indefinite=True,
        position=None,
    )
    repository = Repo(
        Session(
            [
                Result(scalar_rows=[model]),
                Result(scalar_rows=[contract]),
                Result(scalar_rows=[(contract, 1, "ACME")]),
                Result(scalar_rows=[(contract, 1, "ACME")]),
            ]
        )
    )
    assert len(await repository.list_employers()) == 1
    assert len(await repository.list_contracts()) == 1
    assert len(await repository.list_contracts(1)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        EmploymentContractMaintenanceDTO(
            None, 1, date(2026, 1, 1), date(2026, 1, 2), True, None
        ),
        EmploymentContractMaintenanceDTO(None, 1, date(2026, 1, 1), None, False, None),
        EmploymentContractMaintenanceDTO(
            None, 1, date(2026, 2, 1), date(2026, 1, 1), False, None
        ),
    ],
)
async def test_maintain_contracts_rejects_all_invalid_date_shapes(
    command: EmploymentContractMaintenanceDTO,
) -> None:
    """Reject every invalid contract date shape."""
    with pytest.raises(errors.PayrollValidationError):
        await Repo(Session([])).maintain_contracts([command])


@pytest.mark.asyncio
async def test_delete_employers_rejects_missing_and_blocked() -> None:
    """Reject missing and dependent employers."""
    with pytest.raises(errors.PayrollNotFoundError):
        await Repo(Session([Result(scalar_rows=[])])).delete_employers([1])
    session = Session(
        [
            Result(scalar_rows=[employer_model()]),
            Result(scalar_rows=[1]),
            Result(scalar_rows=[]),
        ]
    )
    with pytest.raises(errors.PayrollConflictError):
        await Repo(session).delete_employers([1])


@pytest.mark.asyncio
async def test_delete_employers_succeeds_without_dependents() -> None:
    """Delete an employer without dependents."""
    session = Session(
        [
            Result(scalar_rows=[employer_model()]),
            Result(scalar_rows=[]),
            Result(scalar_rows=[]),
        ]
    )
    await Repo(session).delete_employers([1])
    assert len(session.statements) == 4


@pytest.mark.asyncio
async def test_delete_contracts_rejects_missing_and_payroll_blocker() -> None:
    """Reject missing contracts and contracts referenced by a payroll month."""
    with pytest.raises(errors.PayrollNotFoundError):
        await Repo(Session([Result(scalar_rows=[])])).delete_contracts([1])
    contract = payroll.EmploymentContractModel(
        id=1,
        employer_id=1,
        started_at=date(2026, 1, 1),
        ended_at=None,
        is_indefinite=True,
        position=None,
    )
    period = payroll.PayrollPeriodModel(
        id=8,
        employer_id=1,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        worked_days=30,
    )
    session = Session([Result(scalar_rows=[contract]), Result(scalar_rows=[period])])
    with pytest.raises(errors.PayrollConflictError):
        await Repo(session).delete_contracts([1])


@pytest.mark.asyncio
async def test_delete_contracts_succeeds_without_payroll_blocker() -> None:
    """Delete a contract without a payroll in its month."""
    contract = payroll.EmploymentContractModel(
        id=1,
        employer_id=1,
        started_at=date(2026, 1, 1),
        ended_at=date(2026, 1, 5),
        is_indefinite=False,
        position=None,
    )
    session = Session([Result(scalar_rows=[contract]), Result(scalar_rows=[])])
    await Repo(session).delete_contracts([1])
    assert len(session.statements) == 3
