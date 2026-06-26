"""Reference-data routes."""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, Query

from payroll.application.use_cases.reference_data import ReferenceDataQueries
from payroll.interfaces.api.dependencies import (
    get_reference_data_queries,
)
from pydantic import BaseModel

router = APIRouter(prefix="/reference-data", tags=["reference-data"])


class PensionInstitutionRead(BaseModel):
    """Represent Pension Institution Read."""

    code: str
    name: str
    mandatory_rate: str
    is_active: bool


class HealthInstitutionRead(BaseModel):
    """Represent Health Institution Read."""

    code: str
    name: str
    kind: str
    mandatory_rate: str
    is_active: bool


class PensionPlanRead(BaseModel):
    """Represent Pension Plan Read."""

    id: int
    institution_code: str
    institution_name: str
    valid_from: date
    valid_to: date | None
    additional_rate: str


class HealthPlanRead(BaseModel):
    """Represent Health Plan Read."""

    id: int
    institution_code: str
    institution_name: str
    institution_kind: str
    valid_from: date
    valid_to: date | None
    plan_name: str | None
    contracted_uf: str


class ContributionCapRead(BaseModel):
    """Represent Contribution Cap Read."""

    cap_type: str
    valid_from: date
    valid_to: date | None
    value_uf: str


class PayrollConceptRead(BaseModel):
    """Represent Payroll Concept Read."""

    code: str
    name: str
    kind: str
    is_taxable: bool


@router.get("/pension-institutions", response_model=list[PensionInstitutionRead])
async def list_pension_institutions(
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[PensionInstitutionRead]:
    """List pension institutions."""
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
    include_inactive: bool = Query(default=False),
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[HealthInstitutionRead]:
    """List health institutions."""
    return [
        HealthInstitutionRead(
            code=item.code,
            name=item.name,
            kind=item.kind.value,
            mandatory_rate=str(item.mandatory_rate),
            is_active=item.is_active,
        )
        for item in await queries.list_health_institutions(
            include_inactive=include_inactive
        )
    ]


@router.get("/pension-plans", response_model=list[PensionPlanRead])
async def list_pension_plans(
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[PensionPlanRead]:
    """List pension plans."""
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
    include_inactive: bool = Query(default=False),
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[HealthPlanRead]:
    """List health plans."""
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
        for item in await queries.list_health_plans(include_inactive=include_inactive)
    ]


@router.get("/contribution-caps", response_model=list[ContributionCapRead])
async def list_contribution_caps(
    queries: ReferenceDataQueries = Depends(get_reference_data_queries),
) -> list[ContributionCapRead]:
    """List contribution caps."""
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
    """List payroll concepts."""
    return [
        PayrollConceptRead(**asdict(item))
        for item in await queries.list_payroll_concepts()
    ]
