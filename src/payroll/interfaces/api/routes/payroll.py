"""Payroll routes."""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.interfaces.api.dependencies import get_import_payroll_use_case

router = APIRouter(prefix="/payroll", tags=["payroll"])


class ImportedPayrollPeriodRead(BaseModel):
    id: int
    employer: str
    period_year: int
    period_month: int
    payment_date: date
    status: str
    item_count: int


class ImportPayrollResponse(BaseModel):
    imported_periods: int
    imported_items: int
    periods: list[ImportedPayrollPeriodRead]


@router.post("/import", response_model=ImportPayrollResponse)
async def import_payroll(
    file: UploadFile = File(...),
    use_case: ImportPayroll = Depends(get_import_payroll_use_case),
) -> ImportPayrollResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="A payroll file name is required.")

    try:
        result = await use_case.from_bytes(file.filename, await file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ImportPayrollResponse(
        imported_periods=result.imported_periods,
        imported_items=result.imported_items,
        periods=[ImportedPayrollPeriodRead(**asdict(period)) for period in result.periods],
    )
