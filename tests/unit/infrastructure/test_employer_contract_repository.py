"""Tests for employer and contract maintenance persistence."""

from datetime import date

import pytest

from payroll.application.dto import (
    EmployerMaintenanceDTO,
    EmploymentContractMaintenanceDTO,
)
from payroll.application.errors import (
    PayrollConflictError,
    PayrollNotFoundError,
    PayrollValidationError,
)
from payroll.infrastructure.db.models import EmployerModel
from payroll.infrastructure.db.models.payroll import (
    EmploymentContractModel,
    EmployerFixedDayRoll,
    EmployerPaymentDateRule,
    PayrollPeriodModel,
)
from payroll.infrastructure.db.repositories.payroll_repository import (
    SqlAlchemyPayrollRepository,
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


def employer_model(id: int = 1) -> EmployerModel:
    """Build a fully populated employer model."""
    model = EmployerModel(id=id, name="ACME", country_code="CL", tax_id=None)
    model.payment_date_rule = EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH
    model.payment_fixed_day_roll = EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY
    return model


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


@pytest.mark.asyncio
async def test_maintain_employers_creates_and_updates() -> None:
    """Create and update employers in one batch."""
    existing = employer_model(1)
    session = Session([Result(scalar_one=existing)])
    result = await SqlAlchemyPayrollRepository(session).maintain_employers(
        [employer_command(), employer_command(1)]
    )
    assert len(result.employers) == 2
    assert len(session.added) == 1


@pytest.mark.asyncio
async def test_maintain_employers_rejects_unknown_update() -> None:
    """Reject an update targeting an unknown employer."""
    session = Session([Result(scalar_one=None)])
    with pytest.raises(PayrollNotFoundError):
        await SqlAlchemyPayrollRepository(session).maintain_employers(
            [employer_command(9)]
        )


@pytest.mark.asyncio
async def test_maintain_contracts_creates_and_updates() -> None:
    """Create and update contracts after employer validation."""
    existing = EmploymentContractModel(
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
    result = await SqlAlchemyPayrollRepository(session).maintain_contracts(
        [contract_command(), contract_command(4)]
    )
    assert len(result.contracts) == 2
    with pytest.raises(PayrollNotFoundError):
        await SqlAlchemyPayrollRepository(
            Session([Result(scalar_one=None)])
        ).maintain_contracts([contract_command(99)])


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
    with pytest.raises(PayrollNotFoundError):
        await SqlAlchemyPayrollRepository(session).maintain_contracts(
            [contract_command(99)]
        )
    """Reject invalid interval and overlapping contracts."""
    bad = EmploymentContractMaintenanceDTO(
        id=None,
        employer_id=1,
        started_at=date(2026, 1, 1),
        ended_at=None,
        is_indefinite=False,
        position="Engineer",
    )
    with pytest.raises(PayrollValidationError):
        await SqlAlchemyPayrollRepository(Session([])).maintain_contracts([bad])
    conflict = EmploymentContractModel(
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
    with pytest.raises(PayrollConflictError):
        await SqlAlchemyPayrollRepository(session).maintain_contracts(
            [contract_command()]
        )


@pytest.mark.asyncio
async def test_list_employers_and_contracts() -> None:
    """List employers and contracts."""
    model = employer_model()
    contract = EmploymentContractModel(
        id=1,
        employer_id=1,
        started_at=date(2026, 1, 1),
        ended_at=None,
        is_indefinite=True,
        position=None,
    )
    repository = SqlAlchemyPayrollRepository(
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
    with pytest.raises(PayrollValidationError):
        await SqlAlchemyPayrollRepository(Session([])).maintain_contracts([command])


@pytest.mark.asyncio
async def test_delete_employers_rejects_missing_and_blocked() -> None:
    """Reject missing and dependent employers."""
    with pytest.raises(PayrollNotFoundError):
        await SqlAlchemyPayrollRepository(
            Session([Result(scalar_rows=[])])
        ).delete_employers([1])
    session = Session(
        [
            Result(scalar_rows=[employer_model()]),
            Result(scalar_rows=[1]),
            Result(scalar_rows=[]),
        ]
    )
    with pytest.raises(PayrollConflictError):
        await SqlAlchemyPayrollRepository(session).delete_employers([1])


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
    await SqlAlchemyPayrollRepository(session).delete_employers([1])
    assert len(session.statements) == 4


@pytest.mark.asyncio
async def test_delete_contracts_rejects_missing_and_payroll_blocker() -> None:
    """Reject missing contracts and contracts referenced by a payroll month."""
    with pytest.raises(PayrollNotFoundError):
        await SqlAlchemyPayrollRepository(
            Session([Result(scalar_rows=[])])
        ).delete_contracts([1])
    contract = EmploymentContractModel(
        id=1,
        employer_id=1,
        started_at=date(2026, 1, 1),
        ended_at=None,
        is_indefinite=True,
        position=None,
    )
    period = PayrollPeriodModel(
        id=8,
        employer_id=1,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        worked_days=30,
    )
    session = Session([Result(scalar_rows=[contract]), Result(scalar_rows=[period])])
    with pytest.raises(PayrollConflictError):
        await SqlAlchemyPayrollRepository(session).delete_contracts([1])


@pytest.mark.asyncio
async def test_delete_contracts_succeeds_without_payroll_blocker() -> None:
    """Delete a contract without a payroll in its month."""
    contract = EmploymentContractModel(
        id=1,
        employer_id=1,
        started_at=date(2026, 1, 1),
        ended_at=date(2026, 1, 5),
        is_indefinite=False,
        position=None,
    )
    session = Session([Result(scalar_rows=[contract]), Result(scalar_rows=[])])
    await SqlAlchemyPayrollRepository(session).delete_contracts([1])
    assert len(session.statements) == 3
