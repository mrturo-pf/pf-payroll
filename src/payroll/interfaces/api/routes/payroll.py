"""Payroll routes."""

from dataclasses import asdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile
from pydantic import BaseModel

from payroll.application.dto import ComputeContributionsCommandDTO
from payroll.application.use_cases.compute_contributions import ComputeContributions
from payroll.application.use_cases.import_payroll import ImportPayroll
from payroll.interfaces.api.dependencies import (
    get_compute_contributions_use_case,
    get_import_payroll_use_case,
)

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


class ComputeContributionsRequest(BaseModel):
    pension_plan_id: int
    health_plan_id: int
    uf_value_clp: Decimal | None = None


class PensionContributionRead(BaseModel):
    institution_code: str
    taxable_clp: str
    cap_clp: str
    capped_base_clp: str
    base_amount_clp: str
    additional_amount_clp: str


class HealthContributionRead(BaseModel):
    institution_code: str
    institution_kind: str
    taxable_clp: str
    cap_clp: str
    capped_base_clp: str
    base_amount_clp: str
    contracted_uf: str
    contracted_clp: str
    additional_amount_clp: str


class ComputeContributionsResponse(BaseModel):
    period_id: int
    pension_plan_id: int
    health_plan_id: int
    taxable_income_clp: str
    total_discount_clp: str
    pension: PensionContributionRead
    health: HealthContributionRead


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


@router.post("/{period_id}/compute-contributions", response_model=ComputeContributionsResponse)
async def compute_contributions(
    payload: ComputeContributionsRequest,
    period_id: int = Path(..., gt=0),
    use_case: ComputeContributions = Depends(get_compute_contributions_use_case),
) -> ComputeContributionsResponse:
    try:
        result = await use_case.execute(
            ComputeContributionsCommandDTO(
                period_id=period_id,
                pension_plan_id=payload.pension_plan_id,
                health_plan_id=payload.health_plan_id,
                uf_value_clp=payload.uf_value_clp,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ComputeContributionsResponse(
        period_id=result.period_id,
        pension_plan_id=result.pension_plan_id,
        health_plan_id=result.health_plan_id,
        taxable_income_clp=str(result.taxable_income_clp),
        total_discount_clp=str(result.total_discount_clp),
        pension=PensionContributionRead(
            institution_code=result.pension.institution_code,
            taxable_clp=str(result.pension.taxable_clp),
            cap_clp=str(result.pension.cap_clp),
            capped_base_clp=str(result.pension.capped_base_clp),
            base_amount_clp=str(result.pension.base_amount_clp),
            additional_amount_clp=str(result.pension.additional_amount_clp),
        ),
        health=HealthContributionRead(
            institution_code=result.health.institution_code,
            institution_kind=result.health.institution_kind.value,
            taxable_clp=str(result.health.taxable_clp),
            cap_clp=str(result.health.cap_clp),
            capped_base_clp=str(result.health.capped_base_clp),
            base_amount_clp=str(result.health.base_amount_clp),
            contracted_uf=str(result.health.contracted_uf),
            contracted_clp=str(result.health.contracted_clp),
            additional_amount_clp=str(result.health.additional_amount_clp),
        ),
    )
