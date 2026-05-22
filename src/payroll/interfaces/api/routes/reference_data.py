"""Reference-data routes."""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.interfaces.api.dependencies import get_reference_data_queries

router = APIRouter(prefix="/reference-data", tags=["reference-data"])


class CurrencyRead(BaseModel):
    code: str
    name: str
    is_fiat: bool
    unit_kind: str


class PensionInstitutionRead(BaseModel):
    code: str
    name: str
    mandatory_rate: str
    is_active: bool


class HealthInstitutionRead(BaseModel):
    code: str
    name: str
    kind: str
    mandatory_rate: str
    is_active: bool


class PensionPlanRead(BaseModel):
    id: int
    institution_code: str
    institution_name: str
    valid_from: date
    valid_to: date | None
    additional_rate: str


class HealthPlanRead(BaseModel):
    id: int
    institution_code: str
    institution_name: str
    institution_kind: str
    valid_from: date
    valid_to: date | None
    plan_name: str | None
    contracted_uf: str


class ContributionCapRead(BaseModel):
    cap_type: str
    valid_from: date
    valid_to: date | None
    value_uf: str


class PayrollConceptRead(BaseModel):
    code: str
    name: str
    kind: str
    is_taxable: bool


class IncomeTaxBracketRead(BaseModel):
    valid_from: date
    valid_to: date | None
    lower_bound_utm: str
    upper_bound_utm: str | None
    marginal_rate: str
    rebate_utm: str


@router.get("/currencies", response_model=list[CurrencyRead])
async def list_currencies(
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[CurrencyRead]:
    return [CurrencyRead(**asdict(item)) for item in await queries.list_currencies()]


@router.get("/pension-institutions", response_model=list[PensionInstitutionRead])
async def list_pension_institutions(
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[PensionInstitutionRead]:
    return [
        PensionInstitutionRead(
            code=item.code,
            name=item.name,
            mandatory_rate=str(item.mandatory_rate),
            is_active=item.is_active,
        )
        for item in await queries.list_pension_institutions()
    ]


@router.get("/health-institutions", response_model=list[HealthInstitutionRead])
async def list_health_institutions(
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[HealthInstitutionRead]:
    return [
        HealthInstitutionRead(
            code=item.code,
            name=item.name,
            kind=item.kind.value,
            mandatory_rate=str(item.mandatory_rate),
            is_active=item.is_active,
        )
        for item in await queries.list_health_institutions()
    ]


@router.get("/pension-plans", response_model=list[PensionPlanRead])
async def list_pension_plans(
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[PensionPlanRead]:
    return [
        PensionPlanRead(
            id=item.id,
            institution_code=item.institution_code,
            institution_name=item.institution_name,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            additional_rate=str(item.additional_rate),
        )
        for item in await queries.list_pension_plans()
    ]


@router.get("/health-plans", response_model=list[HealthPlanRead])
async def list_health_plans(
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[HealthPlanRead]:
    return [
        HealthPlanRead(
            id=item.id,
            institution_code=item.institution_code,
            institution_name=item.institution_name,
            institution_kind=item.institution_kind.value,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            plan_name=item.plan_name,
            contracted_uf=str(item.contracted_uf),
        )
        for item in await queries.list_health_plans()
    ]


@router.get("/contribution-caps", response_model=list[ContributionCapRead])
async def list_contribution_caps(
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[ContributionCapRead]:
    return [
        ContributionCapRead(
            cap_type=item.cap_type,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            value_uf=str(item.value_uf),
        )
        for item in await queries.list_contribution_caps()
    ]


@router.get("/payroll-concepts", response_model=list[PayrollConceptRead])
async def list_payroll_concepts(
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[PayrollConceptRead]:
    return [PayrollConceptRead(**asdict(item)) for item in await queries.list_payroll_concepts()]


@router.get("/income-tax-brackets", response_model=list[IncomeTaxBracketRead])
async def list_income_tax_brackets(
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[IncomeTaxBracketRead]:
    return [
        IncomeTaxBracketRead(
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            lower_bound_utm=str(item.lower_bound_utm),
            upper_bound_utm=str(item.upper_bound_utm) if item.upper_bound_utm is not None else None,
            marginal_rate=str(item.marginal_rate),
            rebate_utm=str(item.rebate_utm),
        )
        for item in await queries.list_income_tax_brackets()
    ]
