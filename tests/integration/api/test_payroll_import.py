from datetime import date
from io import BytesIO

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from payroll.application.dto import ImportPayrollResultDTO, ImportedPayrollPeriodDTO
from payroll.interfaces.api.dependencies import get_import_payroll_use_case
from payroll.interfaces.api.main import app
from payroll.interfaces.api.routes.payroll import import_payroll


class FakeImportPayroll:
    async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
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
                    status="projected",
                    item_count=1,
                )
            ],
        )


def test_payroll_import_endpoint() -> None:
    app.dependency_overrides[get_import_payroll_use_case] = lambda: FakeImportPayroll()
    client = TestClient(app)

    try:
        response = client.post(
            "/payroll/import",
            files={"file": ("sample.csv", b"period,employer,payment_date,salary_base\nJan/2026,ACME,2026-01-31,1000000\n", "text/csv")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "imported_periods": 1,
        "imported_items": 1,
        "periods": [
            {
                "id": 1,
                "employer": "ACME",
                "period_year": 2026,
                "period_month": 1,
                "payment_date": "2026-01-31",
                "status": "projected",
                "item_count": 1,
            }
        ],
    }


def test_payroll_import_endpoint_requires_filename_and_surfaces_value_errors() -> None:
    class ErrorImportPayroll:
        async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
            raise ValueError("bad payroll file")

    app.dependency_overrides[get_import_payroll_use_case] = lambda: ErrorImportPayroll()
    client = TestClient(app)

    try:
        missing_name = client.post("/payroll/import", files={"file": ("", b"noop", "text/csv")})
        invalid_file = client.post("/payroll/import", files={"file": ("bad.csv", b"noop", "text/csv")})
    finally:
        app.dependency_overrides.clear()

    assert missing_name.status_code == 422
    assert invalid_file.status_code == 400
    assert invalid_file.json() == {"detail": "bad payroll file"}


@pytest.mark.asyncio
async def test_payroll_import_endpoint_rejects_empty_filename_in_handler() -> None:
    with pytest.raises(HTTPException, match="A payroll file name is required."):
        await import_payroll(UploadFile(file=BytesIO(b"noop"), filename=""), FakeImportPayroll())
