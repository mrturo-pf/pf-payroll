"""Tests for test payroll import."""

from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Literal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from payroll.application.errors import (
    PayrollDependencyError,
    PayrollValidationError,
)
from payroll.application.dto import (
    ImportPayrollResultDTO,
    ImportedPayrollPeriodDTO,
)

from payroll.interfaces.api.dependencies import (
    get_transactional_import_payroll_use_case,
    get_transactional_process_imported_payroll_periods_use_case,
    get_transactional_session,
)
from payroll.interfaces.api.main import app
from payroll.interfaces.api.routes.payroll import (
    import_payroll,
)


class FakeTransactionalSessionScope:
    """Test double for TransactionalSessionScope -- records resolve() calls.

    A deliberate, jscpd-exempted mirror of the identically-shaped double in
    test_payroll_import_json.py -- POST /payroll/import/spreadsheet now runs on the
    exact same transactional-scope machinery.
    """

    # jscpd:ignore-start
    def __init__(self) -> None:
        """Initialize the instance."""
        self.resolved_with: list[str] = []
        self.session = object()

    async def resolve(self, mode: Literal["commit", "validate"]) -> None:
        """Record the resolution instead of touching a real transaction."""
        self.resolved_with.append(mode)

    # jscpd:ignore-end


def _override_import_dependencies(
    scope: FakeTransactionalSessionScope,
    use_case: object,
    process_use_case: object | None = None,
) -> None:
    """Wire the fake import/process use cases + a fake scope into the app.

    Shared by every /payroll/import/spreadsheet test below to avoid repeating this same
    three-dependency wiring per test -- mirrors
    test_payroll_import_json.py's own _override_happy_path() helper.
    process_use_case defaults to the untouched-passthrough double; pass an
    explicit one (e.g. a fake that raises) to simulate a downstream failure
    instead.
    """
    app.dependency_overrides[get_transactional_session] = lambda: scope
    app.dependency_overrides[get_transactional_import_payroll_use_case] = lambda: (
        use_case
    )
    app.dependency_overrides[
        get_transactional_process_imported_payroll_periods_use_case
    ] = lambda: process_use_case or FakeProcessImportedPayrollPeriods()


class FakeImportPayroll:
    """Test double for Import Payroll."""

    async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
        """Create from bytes."""
        assert filename == "sample.csv"
        assert b"salary_base" in content
        return ImportPayrollResultDTO(
            imported_periods=1,
            imported_items=1,
            periods=[
                ImportedPayrollPeriodDTO(
                    id=1,
                    employer="ACME",
                    period_year=2026,
                    period_month=1,
                    payment_date=date(2026, 1, 31),
                    item_count=1,
                    declared_net_pay_clp=Decimal("950000"),
                    expected_net_pay_clp=None,
                    net_pay_difference_clp=None,
                    net_pay_warning=(
                        "Declared net_pay will be reconciled after computed "
                        "contributions and income tax are generated."
                    ),
                )
            ],
        )


class FakeProcessImportedPayrollPeriods:
    """Test double for imported-payroll post-processing."""

    async def execute(self, result: ImportPayrollResultDTO) -> ImportPayrollResultDTO:
        """Handle execute."""
        return result


class FakeImportPayrollWithConflict:
    """Test double whose sole period has a real net_pay mismatch.

    Shared by both the commit-mode-rejects and validate-mode-reports tests
    below (same fixture, two different `mode` values through the route) --
    also a deliberate, jscpd-exempted mirror of the analogous fixture in
    test_payroll_import_json.py's own genuine-conflict tests.
    """

    # jscpd:ignore-start
    async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
        """Return a period whose declared net pay does not reconcile."""
        return ImportPayrollResultDTO(
            imported_periods=1,
            imported_items=1,
            periods=[
                ImportedPayrollPeriodDTO(
                    id=1,
                    employer="ACME",
                    period_year=2026,
                    period_month=1,
                    payment_date=date(2026, 1, 31),
                    item_count=1,
                    declared_net_pay_clp=Decimal("950000"),
                    expected_net_pay_clp=Decimal("900000"),
                    net_pay_difference_clp=Decimal("50000"),
                    net_pay_warning=(
                        "Declared net_pay does not match the fully "
                        "computed payroll totals. Difference: 50000 CLP."
                    ),
                )
            ],
        )

    # jscpd:ignore-end


def _post_deflate_amounts(client: TestClient) -> object:
    return client.post(
        "/payroll/5/deflate", json={"target_year": 2026, "target_month": 3}
    )


def test_payroll_import_endpoint() -> None:
    """Test payroll import endpoint."""
    scope = FakeTransactionalSessionScope()
    _override_import_dependencies(scope, FakeImportPayroll())
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/spreadsheet",
            files={
                "file": (
                    "sample.csv",
                    (
                        b"period_month,period_year,employer,payment_date,employment_contract_kind,salary_base\n"
                        b"1,2026,ACME,2026-01-31,indefinite,1000000\n"
                    ),
                    "text/csv",
                )
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert scope.resolved_with == ["commit"]
    assert response.json() == {
        "mode": "commit",
        "validated": True,
        "saved": True,
        "period_count": 1,
        "item_count": 1,
        "validated_period_count": 1,
        "unvalidated_period_count": 0,
        "periods": [
            {
                "id": 1,
                "employer": "ACME",
                "period_year": 2026,
                "period_month": 1,
                "payment_date": "2026-01-31",
                "worked_days": 30,
                "item_count": 1,
                "declared_net_pay_clp": 950000,
                "expected_net_pay_clp": None,
                "net_pay_difference_clp": None,
                "net_pay_warning": (
                    "Declared net_pay will be reconciled after computed "
                    "contributions and income tax are generated."
                ),
                "validated": True,
                "contribution_validation": None,
                "complementary_insurance_validation": None,
            }
        ],
        "unresolved_rows": [],
    }


def test_payroll_import_endpoint_rejects_commit_on_genuine_conflict() -> None:
    """mode="commit" refuses to persist when a period has a real conflict.

    A period with a genuine declared-vs-computed net_pay mismatch (i.e.
    expected_net_pay_clp is actually populated, unlike the "pending"
    projected-period case exercised by test_payroll_import_endpoint) must
    roll everything back and fail the whole request (422,
    PayrollImportNotValidatedError) instead of persisting
    partially-reconciled data.
    """
    scope = FakeTransactionalSessionScope()
    _override_import_dependencies(scope, FakeImportPayrollWithConflict())
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/spreadsheet",
            files={"file": ("sample.csv", b"data", "text/csv")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    # jscpd:ignore-start -- deliberate mirror of the analogous assertions in
    # test_payroll_import_json.py's own genuine-conflict test.
    detail = response.json()["detail"]
    assert "Cannot commit" in detail["message"]
    assert len(detail["conflicting_periods"]) == 1
    conflicting = detail["conflicting_periods"][0]
    assert conflicting["id"] is None  # rolled back -- see ImportedPeriodRead
    assert conflicting["period_year"] == 2026
    assert conflicting["period_month"] == 1
    assert conflicting["net_pay_difference_clp"] == 50000
    # jscpd:ignore-end
    assert scope.resolved_with == ["validate"]


def test_payroll_import_endpoint_accepts_explicit_commit_mode() -> None:
    """mode="commit" sent explicitly as a form field behaves like the default.

    Mirrors test_import_payroll_rows_endpoint_accepts_explicit_commit_mode
    -- this endpoint takes `mode` as a multipart form field (alongside
    `file`), not JSON, since it is a file upload, but the contract is
    otherwise identical to POST /payroll/import/json.
    """
    scope = FakeTransactionalSessionScope()
    _override_import_dependencies(scope, FakeImportPayroll())
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/spreadsheet",
            data={"mode": "commit"},
            files={"file": ("sample.csv", b"salary_base", "text/csv")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["mode"] == "commit"
    assert scope.resolved_with == ["commit"]


def test_payroll_import_endpoint_validate_mode_never_commits() -> None:
    """mode="validate" runs the full pipeline but resolves the scope as validate.

    A deliberate mirror of
    test_import_payroll_rows_endpoint_validate_mode_never_commits: same
    contract, reached via the CSV/XLSX upload endpoint instead of the
    already-structured-rows one.
    """
    scope = FakeTransactionalSessionScope()
    _override_import_dependencies(scope, FakeImportPayroll())
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/spreadsheet",
            data={"mode": "validate"},
            files={"file": ("sample.csv", b"salary_base", "text/csv")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    # jscpd:ignore-start -- deliberate mirror of the analogous assertions in
    # test_payroll_import_json.py's own validate-mode-never-commits test.
    assert body["mode"] == "validate"
    assert body["validated"] is True
    assert body["saved"] is None
    assert body["period_count"] == 1
    assert scope.resolved_with == ["validate"]
    # Nulled out on purpose -- see ImportedPeriodRead's docstring: the
    # SAVEPOINT's INSERT genuinely happened, but the outer transaction was
    # rolled back, so this id will never exist for real.
    assert body["periods"][0]["id"] is None
    # jscpd:ignore-end


def test_payroll_import_endpoint_validate_mode_rejects_genuine_conflict_with_422() -> (
    None
):
    """mode="validate" surfaces a genuine conflict as a 422, not a 200.

    mode="commit" (see test_payroll_import_endpoint_rejects_commit_on_genuine_conflict)
    and mode="validate" both always roll back, and both now raise the exact
    same PayrollImportNotValidatedError (422) instead of either the old
    "200 with validated=False" shape (validate) or a plain 400
    (commit's old status) -- carrying the same conflicting_periods detail
    either way so a caller can still preview every conflict in one shot.
    """
    scope = FakeTransactionalSessionScope()
    _override_import_dependencies(scope, FakeImportPayrollWithConflict())
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/spreadsheet",
            data={"mode": "validate"},
            files={"file": ("sample.csv", b"data", "text/csv")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Import validation failed" in detail["message"]
    assert len(detail["conflicting_periods"]) == 1
    conflicting = detail["conflicting_periods"][0]
    assert conflicting["id"] is None  # rolled back -- see ImportedPeriodRead
    assert conflicting["net_pay_difference_clp"] == 50000
    assert scope.resolved_with == ["validate"]


def test_payroll_import_returns_502_when_processing_raises_dependency_error() -> None:
    """Processing failure from a dependency returns 502 with error detail."""

    class FakeImportPayrollOK:
        """Test double — import always succeeds with an empty result."""

        async def from_bytes(
            self, filename: str, content: bytes
        ) -> ImportPayrollResultDTO:
            """Return an empty successful import."""
            return ImportPayrollResultDTO(
                imported_periods=0, imported_items=0, periods=[]
            )

    class FakeProcessRaisesDependencyError:
        """Test double — processing always raises a dependency error."""

        async def execute(
            self, result: ImportPayrollResultDTO
        ) -> ImportPayrollResultDTO:
            """Raise to simulate pf-rates being unreachable."""
            raise PayrollDependencyError(
                "Network error fetching exchange rate from pf-rates: missing protocol"
            )

    scope = FakeTransactionalSessionScope()
    _override_import_dependencies(
        scope, FakeImportPayrollOK(), FakeProcessRaisesDependencyError()
    )
    client = TestClient(
        app, headers={"X-API-Key": "test-key"}, raise_server_exceptions=False
    )

    try:
        response = client.post(
            "/payroll/import/spreadsheet",
            files={"file": ("payroll.csv", b"data", "text/csv")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert "pf-rates" in response.json()["detail"]
    assert scope.resolved_with == ["validate"]


def test_payroll_import_endpoint_requires_filename_and_surfaces_value_errors() -> None:
    """Test payroll import endpoint requires filename and surfaces value errors."""

    class ErrorImportPayroll:
        """Represent the error import payroll."""

        async def from_bytes(
            self, filename: str, content: bytes
        ) -> ImportPayrollResultDTO:
            """Create from bytes."""
            raise PayrollValidationError("bad payroll file")

    scope = FakeTransactionalSessionScope()
    _override_import_dependencies(scope, ErrorImportPayroll())
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        missing_name = client.post(
            "/payroll/import/spreadsheet", files={"file": ("", b"noop", "text/csv")}
        )
        invalid_file = client.post(
            "/payroll/import/spreadsheet",
            files={"file": ("bad.csv", b"noop", "text/csv")},
        )
    finally:
        app.dependency_overrides.clear()

    assert missing_name.status_code == 422
    assert invalid_file.status_code == 400
    assert invalid_file.json() == {"detail": "bad payroll file"}
    assert scope.resolved_with == ["validate"]


@pytest.mark.asyncio
async def test_payroll_import_endpoint_rejects_empty_filename_in_handler() -> None:
    """Test payroll import endpoint rejects empty filename in handler."""
    with pytest.raises(HTTPException, match="A payroll file name is required."):
        await import_payroll(
            UploadFile(file=BytesIO(b"noop"), filename=""),
            FakeImportPayroll(),
            FakeProcessImportedPayrollPeriods(),
        )
