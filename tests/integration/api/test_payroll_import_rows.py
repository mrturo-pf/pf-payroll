"""Tests for POST /payroll/import/rows (stage 3: commit + validate modes)."""

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi.testclient import TestClient

from payroll.application.dto import ImportPayrollResultDTO, ImportedPayrollPeriodDTO
from payroll.application.errors import PayrollDependencyError, PayrollValidationError
from payroll.domain.contributions import EmploymentContractKind
from payroll.interfaces.api.dependencies import (
    get_import_payroll_use_case_for_rows_import,
    get_process_imported_payroll_periods_use_case_for_rows_import,
    get_transactional_session,
)
from payroll.interfaces.api.main import app

SAMPLE_ROW = {
    "employer": "ACME",
    "period_year": 2026,
    "period_month": 1,
    "payment_date": "2026-01-31",
    "status": "actual",
    "employment_contract_kind": "indefinite",
    "concept_code": "SALARY_BASE",
    "amount_clp": "1000000",
    "worked_days": 30,
    "declared_net_pay_clp": "950000",
}


class FakeTransactionalSessionScope:
    """Test double for TransactionalSessionScope -- records resolve() calls."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.resolved_with: list[str] = []
        self.session = object()

    async def resolve(self, mode: Literal["commit", "validate"]) -> None:
        """Record the resolution instead of touching a real transaction."""
        self.resolved_with.append(mode)


class FakeImportPayrollFromRows:
    """Test double for ImportPayroll.from_rows()."""

    async def from_rows(self, rows: list[object]) -> ImportPayrollResultDTO:
        """Return a canned successful import result."""
        assert rows[0].employer == "ACME"
        assert rows[0].concept_code == "SALARY_BASE"
        return ImportPayrollResultDTO(
            imported_periods=1,
            imported_items=len(rows),
            periods=[
                ImportedPayrollPeriodDTO(
                    id=1,
                    employer="ACME",
                    period_year=2026,
                    period_month=1,
                    payment_date=date(2026, 1, 31),
                    status="actual",
                    employment_contract_kind=EmploymentContractKind.INDEFINITE,
                    item_count=len(rows),
                    declared_net_pay_clp=Decimal("950000"),
                    expected_net_pay_clp=None,
                    net_pay_difference_clp=None,
                    net_pay_warning=None,
                )
            ],
        )


class FakeProcessImportedPayrollPeriods:
    """Test double that passes the import result through untouched."""

    async def execute(self, result: ImportPayrollResultDTO) -> ImportPayrollResultDTO:
        """Return the result unchanged."""
        return result


def _override_happy_path(scope: FakeTransactionalSessionScope) -> None:
    """Wire the fake use cases + a given fake scope into the app."""
    app.dependency_overrides[get_transactional_session] = lambda: scope
    app.dependency_overrides[get_import_payroll_use_case_for_rows_import] = lambda: (
        FakeImportPayrollFromRows()
    )
    app.dependency_overrides[
        get_process_imported_payroll_periods_use_case_for_rows_import
    ] = lambda: FakeProcessImportedPayrollPeriods()


def test_import_payroll_rows_endpoint_defaults_to_commit_mode() -> None:
    """Happy path: posting rows without `mode` resolves the scope as commit."""
    scope = FakeTransactionalSessionScope()
    _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post("/payroll/import/rows", json={"rows": [SAMPLE_ROW]})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["imported_periods"] == 1
    assert body["imported_items"] == 1
    assert body["periods"][0]["employer"] == "ACME"
    assert scope.resolved_with == ["commit"]


def test_import_payroll_rows_endpoint_accepts_explicit_commit_mode() -> None:
    """mode="commit" is accepted explicitly (same behavior as the default)."""
    scope = FakeTransactionalSessionScope()
    _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/rows",
            json={"mode": "commit", "rows": [SAMPLE_ROW]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert scope.resolved_with == ["commit"]


def test_import_payroll_rows_endpoint_validate_mode_never_commits() -> None:
    """mode="validate" runs the full pipeline but resolves the scope as validate."""
    scope = FakeTransactionalSessionScope()
    _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/rows",
            json={"mode": "validate", "rows": [SAMPLE_ROW]},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["imported_periods"] == 1
    assert scope.resolved_with == ["validate"]


def test_import_payroll_rows_endpoint_rejects_unresolved_concept_code() -> None:
    """A row with concept_code=null (unresolved) fails schema validation.

    This is how the design's "commit must reject unresolved concepts"
    requirement is enforced -- ImportPayrollRowRequest.concept_code is a
    required str, so FastAPI/pydantic reject a null value before any
    application code (or the transactional scope) ever runs.
    """
    client = TestClient(app, headers={"X-API-Key": "test-key"})
    row = {**SAMPLE_ROW, "concept_code": None}

    response = client.post("/payroll/import/rows", json={"rows": [row]})

    assert response.status_code == 422


def test_import_payroll_rows_endpoint_rejects_unknown_mode() -> None:
    """A mode outside {commit, validate} is a 422 from the schema itself."""
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    response = client.post(
        "/payroll/import/rows",
        json={"mode": "dry-run", "rows": [SAMPLE_ROW]},
    )

    assert response.status_code == 422


def test_import_payroll_rows_endpoint_rolls_back_on_validation_error() -> None:
    """A failure from from_rows() resolves the scope as validate, not commit.

    Even though mode="commit" was requested, a half-applied import must
    never be left committed -- the route always forces a rollback before
    re-raising any PayrollError.
    """

    class ErrorImportPayroll:
        """Test double whose from_rows() always raises."""

        async def from_rows(self, rows: list[object]) -> ImportPayrollResultDTO:
            """Raise to simulate the use case's empty-rows guard."""
            raise PayrollValidationError(
                "The provided payroll rows list must not be empty."
            )

    scope = FakeTransactionalSessionScope()
    app.dependency_overrides[get_transactional_session] = lambda: scope
    app.dependency_overrides[get_import_payroll_use_case_for_rows_import] = lambda: (
        ErrorImportPayroll()
    )
    app.dependency_overrides[
        get_process_imported_payroll_periods_use_case_for_rows_import
    ] = lambda: FakeProcessImportedPayrollPeriods()
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/rows", json={"mode": "commit", "rows": []}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "must not be empty" in response.json()["detail"]
    assert scope.resolved_with == ["validate"]


def test_import_payroll_rows_endpoint_returns_502_when_processing_raises() -> None:
    """A dependency failure during post-processing surfaces as 502 and rolls back."""

    class FakeProcessRaisesDependencyError:
        """Test double whose execute() always raises a dependency error."""

        async def execute(
            self, result: ImportPayrollResultDTO
        ) -> ImportPayrollResultDTO:
            """Raise to simulate pf-rates being unreachable."""
            raise PayrollDependencyError(
                "Network error fetching exchange rate from pf-rates: missing protocol"
            )

    scope = FakeTransactionalSessionScope()
    app.dependency_overrides[get_transactional_session] = lambda: scope
    app.dependency_overrides[get_import_payroll_use_case_for_rows_import] = lambda: (
        FakeImportPayrollFromRows()
    )
    app.dependency_overrides[
        get_process_imported_payroll_periods_use_case_for_rows_import
    ] = lambda: FakeProcessRaisesDependencyError()
    client = TestClient(
        app, headers={"X-API-Key": "test-key"}, raise_server_exceptions=False
    )

    try:
        response = client.post("/payroll/import/rows", json={"rows": [SAMPLE_ROW]})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert "pf-rates" in response.json()["detail"]
    assert scope.resolved_with == ["validate"]
