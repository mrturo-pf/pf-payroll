"""Tests for POST /payroll/import/json (stage 3: commit + validate modes).

Stage 4 added multi-period support: the request body moved from one set of
header fields + `rows` at the top level to `periods: [...]`, one block per
payslip/period, each carrying its own header fields + `rows`. `_payload()`
below builds that shape; `_period()` builds one block of it.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi.testclient import TestClient

from payroll.application.dto import ImportPayrollResultDTO, ImportedPayrollPeriodDTO
from payroll.application.errors import PayrollDependencyError, PayrollValidationError
from payroll.domain.contributions import EmploymentContractKind
from payroll.interfaces.api.dependencies import (
    get_transactional_import_payroll_use_case,
    get_transactional_process_imported_payroll_periods_use_case,
    get_transactional_session,
)
from payroll.interfaces.api.main import app

SAMPLE_HEADER = {
    "employer": "ACME",
    "period_year": 2026,
    "period_month": 1,
    "payment_date": "2026-01-31",
    "employment_contract_kind": "indefinite",
    "worked_days": 30,
    "declared_net_pay_clp": "950000",
}

SAMPLE_ROW = {
    "concept_code": "SALARY_BASE",
    "amount_clp": "1000000",
}


def _period(
    rows: list[dict[str, object]], **header_overrides: object
) -> dict[str, object]:
    """Build a single `periods[]` block: shared header once + the given rows.

    Mirrors the real request shape: employer/period/payment/contract-kind
    live once per block (same as PdfImportPreviewResponse's own header),
    never repeated per row.
    """
    return {**SAMPLE_HEADER, **header_overrides, "rows": rows}


def _payload(
    rows: list[dict[str, object]], mode: str | None = None
) -> dict[str, object]:
    """Build a full request body with a single period block.

    Kept as the single-period convenience wrapper most tests use --
    multi-period tests build `periods` by hand instead.
    """
    return _payload_periods([_period(rows)], mode=mode)


def _payload_periods(
    periods: list[dict[str, object]], mode: str | None = None
) -> dict[str, object]:
    """Build a full request body from an explicit `periods` list."""
    payload: dict[str, object] = {"periods": periods}
    if mode is not None:
        payload["mode"] = mode
    return payload


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

    def __init__(self) -> None:
        """Initialize the instance."""
        self.called_with: list[object] | None = None

    async def from_rows(self, rows: list[object]) -> ImportPayrollResultDTO:
        """Return a canned successful import result -- one period per distinct key."""
        self.called_with = rows
        assert rows[0].employer == "ACME"
        assert rows[0].concept_code == "SALARY_BASE"
        keys: list[tuple[str, int, int]] = []
        for row in rows:
            key = (row.employer, row.period_year, row.period_month)
            if key not in keys:
                keys.append(key)
        periods = [
            ImportedPayrollPeriodDTO(
                id=index + 1,
                employer=employer,
                period_year=period_year,
                period_month=period_month,
                payment_date=date(period_year, period_month, 28),
                status="actual",
                employment_contract_kind=EmploymentContractKind.INDEFINITE,
                item_count=sum(
                    1
                    for row in rows
                    if (row.employer, row.period_year, row.period_month)
                    == (employer, period_year, period_month)
                ),
                declared_net_pay_clp=Decimal("950000"),
                expected_net_pay_clp=None,
                net_pay_difference_clp=None,
                net_pay_warning=None,
            )
            for index, (employer, period_year, period_month) in enumerate(keys)
        ]
        return ImportPayrollResultDTO(
            imported_periods=len(periods),
            imported_items=len(rows),
            periods=periods,
        )


class FakeProcessImportedPayrollPeriods:
    """Test double that passes the import result through untouched."""

    async def execute(self, result: ImportPayrollResultDTO) -> ImportPayrollResultDTO:
        """Return the result unchanged."""
        return result


def _override_happy_path(
    scope: FakeTransactionalSessionScope,
) -> FakeImportPayrollFromRows:
    """Wire the fake use cases + a given fake scope into the app."""
    fake_import = FakeImportPayrollFromRows()
    app.dependency_overrides[get_transactional_session] = lambda: scope
    app.dependency_overrides[get_transactional_import_payroll_use_case] = lambda: (
        fake_import
    )
    app.dependency_overrides[
        get_transactional_process_imported_payroll_periods_use_case
    ] = lambda: FakeProcessImportedPayrollPeriods()
    return fake_import


def test_import_payroll_rows_endpoint_defaults_to_commit_mode() -> None:
    """Happy path: posting rows without `mode` resolves the scope as commit."""
    scope = FakeTransactionalSessionScope()
    _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post("/payroll/import/json", json=_payload([SAMPLE_ROW]))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "commit"
    assert body["validated"] is True
    assert body["saved"] is True
    assert body["period_count"] == 1
    assert body["item_count"] == 1
    assert body["periods"][0]["employer"] == "ACME"
    assert body["periods"][0]["id"] == 1
    assert scope.resolved_with == ["commit"]


def test_import_payroll_rows_endpoint_accepts_explicit_commit_mode() -> None:
    """mode="commit" is accepted explicitly (same behavior as the default)."""
    scope = FakeTransactionalSessionScope()
    _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/json",
            json=_payload([SAMPLE_ROW], mode="commit"),
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
            "/payroll/import/json",
            json=_payload([SAMPLE_ROW], mode="validate"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    # jscpd:ignore-start -- deliberate mirror of the analogous assertions in
    # test_payroll_import.py's own validate-mode-never-commits test.
    assert body["mode"] == "validate"
    assert body["validated"] is True
    assert body["saved"] is None
    assert body["period_count"] == 1
    assert scope.resolved_with == ["validate"]
    # The use case's DTO carries a real id (the INSERT genuinely happened
    # inside the SAVEPOINT), but that id is nulled out here: it will never
    # exist once the outer transaction is rolled back -- see
    # ImportedPeriodRead's docstring for why Postgres itself never gives
    # this value back either (BIGSERIAL sequences are not transactional).
    assert body["periods"][0]["id"] is None
    # jscpd:ignore-end


def test_import_payroll_rows_endpoint_commit_rejects_unresolved_concept_code() -> None:
    """mode="commit" fails outright (422) if any row has no concept_code.

    Per the design recommendation (section 3), commit must reject explicitly
    -- unlike validate (see the tests below), which reports these rows back
    instead of failing. concept_code is nullable at the schema level (so a
    caller can submit a still-unresolved row at all), but the route itself
    enforces the business rule before calling any use case.
    """
    scope = FakeTransactionalSessionScope()
    fake_import = _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})
    row = {**SAMPLE_ROW, "concept_code": None}

    try:
        response = client.post("/payroll/import/json", json=_payload([row]))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "(0, 0)" in detail["message"]
    assert detail["unresolved_rows"] == [
        {"period_index": 0, "row_index": 0, "amount_clp": "1000000"}
    ]
    assert fake_import.called_with is None
    assert scope.resolved_with == ["validate"]


def test_import_payroll_rows_endpoint_validate_rejects_unresolved_rows_with_422() -> (
    None
):
    """mode="validate" rejects unresolved concept_code with 422, not 200.

    Resolved rows still run through the exact same pipeline first (so their
    computed contributions/warnings are genuine -- see fake_import.called_with
    below), but by explicit product decision the request as a whole then
    fails with PayrollImportNotValidatedError (422) instead of the old "200
    with validated=False" shape, reporting the unresolved row(s) back via
    `unresolved_rows` in the error detail, identified by their
    (period_index, row_index) position.
    """
    scope = FakeTransactionalSessionScope()
    fake_import = _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})
    unresolved_row = {"concept_code": None, "amount_clp": "5000"}

    try:
        response = client.post(
            "/payroll/import/json",
            json=_payload([SAMPLE_ROW, unresolved_row], mode="validate"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Import validation failed" in detail["message"]
    assert detail["unresolved_rows"] == [
        {"period_index": 0, "row_index": 1, "amount_clp": "5000"}
    ]
    assert fake_import.called_with is not None
    assert len(fake_import.called_with) == 1
    assert scope.resolved_with == ["validate"]


def test_import_payroll_rows_endpoint_accepts_multiple_periods() -> None:
    """A single request can carry more than one period block.

    This is the whole point of the multi-period upgrade: previously each
    period required its own POST request; now the entire response array
    from a batched POST /payroll/pdf-preview can be submitted together.
    """
    scope = FakeTransactionalSessionScope()
    fake_import = _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})
    periods = [
        _period([SAMPLE_ROW]),
        _period([SAMPLE_ROW], period_month=2, payment_date="2026-02-28"),
    ]

    try:
        response = client.post(
            "/payroll/import/json", json=_payload_periods(periods, mode="commit")
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["period_count"] == 2
    assert body["item_count"] == 2
    assert len(body["periods"]) == 2
    assert body["validated_period_count"] == 2
    assert body["unvalidated_period_count"] == 0
    assert all(period["validated"] for period in body["periods"])
    assert fake_import.called_with is not None
    assert len(fake_import.called_with) == 2
    assert scope.resolved_with == ["commit"]


def test_import_payroll_rows_endpoint_reports_unresolved_rows_across_periods() -> None:
    """unresolved_rows' period_index points at the offending block in the 422 detail.

    Even when it's mixed with a clean period. mode="validate" fails the
    whole batch (422, PayrollImportNotValidatedError) once *any* period has
    an unresolved row, even though this batch also has a genuinely clean
    period alongside it -- see PayrollImportNotValidatedError's docstring
    for why the whole request fails rather than a 200 breaking down
    per-period pass/fail as it used to.
    """
    scope = FakeTransactionalSessionScope()
    _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})
    unresolved_row = {"concept_code": None, "amount_clp": "5000"}
    periods = [
        _period([SAMPLE_ROW]),
        _period([SAMPLE_ROW, unresolved_row], period_month=2),
    ]

    try:
        response = client.post(
            "/payroll/import/json", json=_payload_periods(periods, mode="validate")
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["unresolved_rows"] == [
        {"period_index": 1, "row_index": 1, "amount_clp": "5000"}
    ]
    assert detail["conflicting_periods"] == []


def test_import_payroll_rows_endpoint_rejects_empty_periods_list() -> None:
    """`periods` must have at least one element."""
    scope = FakeTransactionalSessionScope()
    _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/json", json=_payload_periods([], mode="validate")
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "must not be empty" in response.json()["detail"]
    assert scope.resolved_with == ["validate"]


def test_import_payroll_rows_endpoint_rejects_duplicate_period_keys() -> None:
    """Two blocks sharing (employer, period_year, period_month) is a 400.

    Each block owns its own header (payment_date, contract kind, declared
    net pay) -- silently merging two "same period" blocks would mean
    picking one's header over the other's with no signal to the caller.
    """
    scope = FakeTransactionalSessionScope()
    fake_import = _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})
    periods = [_period([SAMPLE_ROW]), _period([SAMPLE_ROW])]

    try:
        response = client.post(
            "/payroll/import/json", json=_payload_periods(periods, mode="validate")
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "ACME" in response.json()["detail"]
    assert fake_import.called_with is None
    assert scope.resolved_with == ["validate"]


def test_import_payroll_rows_endpoint_rejects_commit_on_genuine_conflict() -> None:
    """mode="commit" refuses to persist when a period has a real conflict.

    Every row resolved a concept_code (so the earlier, cheap
    unresolved-rows check never fires), but the reconciliation pipeline
    finds a genuine net_pay mismatch -- this must roll everything back and
    fail the request (422, PayrollImportNotValidatedError) instead of
    committing partially-reconciled data.
    """

    class ConflictingImportPayrollFromRows:
        """Test double whose sole period has a real net_pay mismatch."""

        async def from_rows(self, rows: list[object]) -> ImportPayrollResultDTO:
            """Return a period whose declared net pay does not reconcile."""
            # jscpd:ignore-start -- deliberate mirror of the analogous fixture
            # in test_payroll_import.py's own genuine-conflict test.
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

    scope = FakeTransactionalSessionScope()
    app.dependency_overrides[get_transactional_session] = lambda: scope
    app.dependency_overrides[get_transactional_import_payroll_use_case] = lambda: (
        ConflictingImportPayrollFromRows()
    )
    app.dependency_overrides[
        get_transactional_process_imported_payroll_periods_use_case
    ] = lambda: FakeProcessImportedPayrollPeriods()
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/json", json=_payload([SAMPLE_ROW], mode="commit")
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    # jscpd:ignore-start -- deliberate mirror of the analogous assertions in
    # test_payroll_import.py's own genuine-conflict test.
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


def test_import_payroll_rows_endpoint_validate_all_unresolved_skips_pipeline() -> None:
    """Validate with zero resolved rows never calls from_rows() at all.

    from_rows([]) would otherwise raise "rows must not be empty" -- a
    confusing error for what is really "nothing was resolved yet". The route
    short-circuits to an explicit empty result instead, then still fails the
    request with 422 (PayrollImportNotValidatedError) since nothing was
    validated -- see PayrollImportNotValidatedError's docstring.
    """
    scope = FakeTransactionalSessionScope()
    fake_import = _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})
    row = {**SAMPLE_ROW, "concept_code": None}

    try:
        response = client.post(
            "/payroll/import/json", json=_payload([row], mode="validate")
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Import validation failed" in detail["message"]
    assert detail["conflicting_periods"] == []
    assert detail["unresolved_rows"] == [
        {"period_index": 0, "row_index": 0, "amount_clp": "1000000"}
    ]
    assert fake_import.called_with is None
    assert scope.resolved_with == ["validate"]


def test_import_payroll_rows_endpoint_derives_projected_status_without_net_pay() -> (
    None
):
    """Status is inferred as "projected" when declared_net_pay_clp is absent.

    status is never accepted as caller input on this endpoint at all (see
    ImportPayrollPeriodRequest's docstring) -- it's inferred the exact same
    way xlsx_importer.py already does for CSV/XLSX imports: "actual" once a
    declared net pay is known, "projected" otherwise. The other tests in
    this module all send declared_net_pay_clp, which only exercises the
    "actual" branch.
    """
    scope = FakeTransactionalSessionScope()
    fake_import = _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})
    header_without_net_pay = {
        key: value
        for key, value in SAMPLE_HEADER.items()
        if key != "declared_net_pay_clp"
    }
    payload = _payload_periods([{**header_without_net_pay, "rows": [SAMPLE_ROW]}])

    try:
        response = client.post("/payroll/import/json", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert fake_import.called_with is not None
    assert fake_import.called_with[0].status == "projected"


def test_import_payroll_rows_endpoint_rejects_unknown_mode() -> None:
    """A mode outside {commit, validate} is a 422 from the schema itself.

    Dependencies are overridden for the same reason as the test above: body
    validation does not short-circuit FastAPI's Depends() resolution.
    """
    scope = FakeTransactionalSessionScope()
    _override_happy_path(scope)
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/json",
            json=_payload([SAMPLE_ROW], mode="dry-run"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert scope.resolved_with == []


def test_import_payroll_rows_endpoint_rolls_back_on_validation_error() -> None:
    """A failure from from_rows() resolves the scope as validate, not commit.

    Even though mode="commit" was requested, a half-applied import must
    never be left committed -- the route always forces a rollback before
    re-raising any PayrollError. Submits one period whose `rows` list is
    itself empty (still a non-empty `periods` list, so the new empty-periods
    guard never fires) -- the flattened `rows` passed to from_rows() ends up
    empty either way, exercising its own "must not be empty" guard exactly
    like before this feature.
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
    app.dependency_overrides[get_transactional_import_payroll_use_case] = lambda: (
        ErrorImportPayroll()
    )
    app.dependency_overrides[
        get_transactional_process_imported_payroll_periods_use_case
    ] = lambda: FakeProcessImportedPayrollPeriods()
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post("/payroll/import/json", json=_payload([], mode="commit"))
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
    app.dependency_overrides[get_transactional_import_payroll_use_case] = lambda: (
        FakeImportPayrollFromRows()
    )
    app.dependency_overrides[
        get_transactional_process_imported_payroll_periods_use_case
    ] = lambda: FakeProcessRaisesDependencyError()
    client = TestClient(
        app, headers={"X-API-Key": "test-key"}, raise_server_exceptions=False
    )

    try:
        response = client.post("/payroll/import/json", json=_payload([SAMPLE_ROW]))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert "pf-rates" in response.json()["detail"]
    assert scope.resolved_with == ["validate"]
