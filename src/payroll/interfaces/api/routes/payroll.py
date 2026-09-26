"""Payroll routes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile
from fastapi.responses import Response
from dataclasses import dataclass
from pydantic import BaseModel

from payroll.application.errors import PayrollError, PayrollValidationError
from payroll.application.dto import (
    AssignPlansCommandDTO,
    GeneratedPayrollReportDTO,
    ImportedComplementaryInsuranceValidationDTO,
    ImportedContributionValidationDTO,
    ImportedPayrollPeriodDTO,
    ImportPayrollResultDTO,
    ImportPayrollRowDTO,
    PayrollStatusKind,
    PdfImportPreviewDTO,
    ReviewPayrollPeriodCommandDTO,
    ComputeContributionsCommandDTO,
    DeflateAmountsCommandDTO,
    DeflatedAmountDTO,
    ComputeIncomeTaxCommandDTO,
    PayrollPeriodDetailFields,
    PayrollPeriodRangeFields,
    PayrollPeriodRangeDTO,
    PayrollSummaryDTO,
)
from payroll.domain.contributions import EmploymentContractKind
from payroll.interfaces.api.errors import to_http_exception
from payroll.interfaces.session import TransactionalSessionScope
from payroll.application.use_cases.payroll_queries import PayrollQueries
from payroll.shared.payroll_status import resolve_declared_status
from payroll.interfaces.api.dependencies import (
    get_assign_plans_use_case,
    get_compute_contributions_use_case,
    get_deflate_amounts_use_case,
    get_compute_income_tax_use_case,
    get_import_payroll_use_case,
    get_import_payroll_use_case_for_rows_import,
    get_generate_payroll_report_use_case,
    get_payroll_queries,
    get_preview_pdf_import_use_case,
    get_process_imported_payroll_periods_use_case,
    get_process_imported_payroll_periods_use_case_for_rows_import,
    get_review_payroll_period_use_case,
    get_transactional_session,
)

if TYPE_CHECKING:
    from payroll.application.use_cases.assign_plans import AssignPlans
    from payroll.application.use_cases.compute_contributions import ComputeContributions
    from payroll.application.use_cases.preview_pdf_import import PreviewPdfImport
    from payroll.application.use_cases.compute_income_tax import ComputeIncomeTax
    from payroll.application.use_cases.deflate_amounts import DeflateAmounts
    from payroll.application.use_cases.generate_payroll_report import (
        GeneratePayrollReport,
    )
    from payroll.application.use_cases.import_payroll import ImportPayroll
    from payroll.application.use_cases.process_imported_payroll_periods import (
        ProcessImportedPayrollPeriods,
    )
    from payroll.application.use_cases.review_payroll_period import ReviewPayrollPeriod

router = APIRouter(prefix="/payroll", tags=["payroll"])


class UnresolvedRowWarning(BaseModel):
    """Represent one submitted row whose concept_code could not be resolved.

    Surfaced by POST /payroll/import/rows in mode="validate" so a caller can
    fix these specific rows (identified by their position in the submitted
    `rows` list) before resending with mode="commit". No period/employer
    fields here -- those now live once at ImportPayrollRowsRequest's top
    level, so repeating them per warning would just be duplicate
    information the caller already sent.
    """

    row_index: int
    amount_clp: str


class ImportedPeriodRead(BaseModel):
    """Represent one imported payroll period in an import response.

    Mirrors ImportedPayrollPeriodDTO field-for-field, with one deliberate
    difference: `id` is `int | None` here, not `int`. POST /payroll/import
    always commits, so its periods' `id` is always a real, persisted primary
    key. POST /payroll/import/rows can also run in mode="validate", where
    the INSERT genuinely happens against a SAVEPOINT (so contributions, tax
    and net-pay warnings are computed for real, not guessed) but is always
    rolled back before the response goes out -- see
    TransactionalSessionScope. The id Postgres assigned during that INSERT
    will never exist in the table: Postgres sequences are not transactional,
    so the BIGSERIAL value is permanently consumed regardless of the
    rollback (an intentional, harmless gap -- see BIGSERIAL's own docs), but
    the row itself never persists. Returning that id to the caller as if it
    were a real, reusable reference would be misleading, so it is nulled
    out here instead -- see to_imported_period_read().

    The field list below is a deliberate, jscpd-exempted mirror of
    ImportedPayrollPeriodDTO (not a lazy copy-paste): the interface layer
    needs its own pydantic schema so the public API contract stays stable
    even if the application-layer dataclass shape changes, and vice versa
    -- see AGENTS.md, "DTOs are the only thing crossing layer boundaries".
    """

    # jscpd:ignore-start
    id: int | None
    employer: str
    period_year: int
    period_month: int
    payment_date: date
    status: PayrollStatusKind
    employment_contract_kind: EmploymentContractKind
    item_count: int
    worked_days: int = 30
    declared_net_pay_clp: Decimal | None = None
    expected_net_pay_clp: Decimal | None = None
    net_pay_difference_clp: Decimal | None = None
    net_pay_warning: str | None = None
    contribution_validation: ImportedContributionValidationDTO | None = None
    complementary_insurance_validation: (
        ImportedComplementaryInsuranceValidationDTO | None
    ) = None
    # jscpd:ignore-end


def to_imported_period_read(
    period: ImportedPayrollPeriodDTO, *, mode: Literal["commit", "validate"]
) -> ImportedPeriodRead:
    """Convert an ImportedPayrollPeriodDTO to its API response shape.

    Nulls out `id` when mode == "validate" -- see ImportedPeriodRead's
    docstring for why that id must never be treated as a real reference.
    """
    return ImportedPeriodRead(
        id=period.id if mode == "commit" else None,
        employer=period.employer,
        period_year=period.period_year,
        period_month=period.period_month,
        payment_date=period.payment_date,
        status=period.status,
        employment_contract_kind=period.employment_contract_kind,
        item_count=period.item_count,
        worked_days=period.worked_days,
        declared_net_pay_clp=period.declared_net_pay_clp,
        expected_net_pay_clp=period.expected_net_pay_clp,
        net_pay_difference_clp=period.net_pay_difference_clp,
        net_pay_warning=period.net_pay_warning,
        contribution_validation=period.contribution_validation,
        complementary_insurance_validation=period.complementary_insurance_validation,
    )


class ImportPayrollResponse(BaseModel):
    """Represent Import Payroll Response."""

    imported_periods: int
    imported_items: int
    periods: list[ImportedPeriodRead]
    unresolved_rows: list[UnresolvedRowWarning] = []


class ImportPayrollRowRequest(BaseModel):
    """Represent a single already-structured payroll concept to persist.

    Deliberately just concept_code + amount_clp: employer, period_year,
    period_month, payment_date, employment_contract_kind, worked_days, and
    declared_net_pay_clp all live once at ImportPayrollRowsRequest's top
    level instead of being repeated per row -- every row submitted through
    this endpoint comes from the same single payslip (see
    PdfImportPreviewResponse, which has the identical shape: one set of
    header fields, N rows each carrying only their own concept-level data).
    concept_code is optional here (unlike ImportPayrollRowDTO, where it is
    required) so that a row a human hasn't finished resolving yet can still
    be submitted with mode="validate" -- see ImportPayrollRowsRequest below.
    """

    concept_code: str | None
    amount_clp: Decimal


class ImportPayrollRowsRequest(BaseModel):
    """Represent the request body for POST /payroll/import/rows.

    All rows come from one payslip (e.g. one PdfImportPreviewResponse
    confirmed by a human), so employer/period/payment/contract-kind fields
    are declared once here instead of once per row -- mirroring
    PdfImportPreviewResponse's own header-fields-once, rows-carry-only-
    their-own-data shape. This is deliberately a copy/paste target: take a
    PdfImportPreviewResponse body, add "mode", and POST it here as-is.

    No `status` field on purpose -- it never appears in the source CSV/XLSX
    either (see xlsx_importer.py). It is inferred the exact same way here:
    "actual" once declared_net_pay_clp is known, "projected" otherwise (see
    payroll.shared.payroll_status.resolve_declared_status).

    mode="commit" persists everything, exactly like POST /payroll/import --
    and requires every row to already have a resolved concept_code; any row
    with concept_code=null makes the whole request fail with 400, nothing is
    written. mode="validate" runs the exact same pipeline on the rows that
    *do* have a resolved concept_code -- so contributions, taxes and net-pay
    warnings are genuinely computed -- while rows still missing a
    concept_code are reported back via `unresolved_rows` instead of failing
    the request, then everything is discarded via
    TransactionalSessionScope.resolve("validate").

    Known side effect, in both modes: ProcessImportedPayrollPeriods calls
    pf-rates to resolve missing market data (exchange rates/UTM), and that
    call may cache data in pf-rates' own database. A pf-payroll rollback
    never undoes that -- harmless (public, non-sensitive reference data), but
    mode="validate" is not 100% free of side effects end-to-end.

    Response's `periods[].id` is `null` in mode="validate" on purpose -- see
    ImportedPeriodRead's docstring.
    """

    mode: Literal["commit", "validate"] = "commit"
    employer: str
    period_year: int
    period_month: int
    payment_date: date
    employment_contract_kind: EmploymentContractKind
    worked_days: int = 30
    declared_net_pay_clp: Decimal | None = None
    rows: list[ImportPayrollRowRequest]


class PdfImportPreviewRowRead(BaseModel):
    """Represent a single candidate row in a PDF import preview response."""

    raw_label: str
    amount_clp: str
    kind: str
    concept_code: str | None
    confidence: float


class PdfImportPreviewResponse(BaseModel):
    """Represent Pdf Import Preview Response.

    Never persists anything -- see PreviewPdfImport / TemplatePdfPayrollExtractor.
    This is deliberately shaped so it can be copied verbatim into a POST
    /payroll/import/rows request body (just add "mode" -- template_id is
    ignored there if it's still present). employment_contract_kind is a
    best-effort guess (see TemplatePdfPayrollExtractor's
    _infer_employment_contract_kind), not an authoritative value -- confirm
    or correct it before submitting. See ImportPayrollRowsRequest's
    docstring.
    """

    employer: str | None
    period_year: int | None
    period_month: int | None
    payment_date: date | None
    worked_days: int | None
    declared_net_pay_clp: str | None
    employment_contract_kind: str | None
    template_id: str | None
    rows: list[PdfImportPreviewRowRead]


class ComputeContributionsRequest(BaseModel):
    """Represent Compute Contributions Request."""

    pension_plan_id: int
    health_plan_id: int
    uf_value_clp: Decimal | None = None


class AssignPlansRequest(BaseModel):
    """Represent Assign Plans Request."""

    pension_plan_id: int
    health_plan_id: int


class AssignPlansResponse(BaseModel):
    """Represent Assign Plans Response."""

    period_id: int
    payment_date: date
    pension_plan_id: int
    health_plan_id: int


class ReviewPayrollPeriodResponse(BaseModel):
    """Represent Review Payroll Period Response."""

    period_id: int
    payment_date: date
    status: str


class PensionContributionRead(BaseModel):
    """Represent Pension Contribution Read."""

    institution_code: str
    taxable_clp: str
    cap_clp: str
    capped_base_clp: str
    base_amount_clp: str
    additional_amount_clp: str


class HealthContributionRead(BaseModel):
    """Represent Health Contribution Read."""

    institution_code: str
    institution_kind: str
    taxable_clp: str
    cap_clp: str
    capped_base_clp: str
    base_amount_clp: str
    contracted_uf: str
    contracted_clp: str
    additional_amount_clp: str


class UnemploymentContributionRead(BaseModel):
    """Represent Unemployment Contribution Read."""

    contract_kind: str
    taxable_clp: str
    cap_clp: str
    capped_base_clp: str
    employee_rate: str
    employee_amount_clp: str
    employer_rate: str
    employer_amount_clp: str


class ComputeContributionsResponse(BaseModel):
    """Represent Compute Contributions Response."""

    period_id: int
    pension_plan_id: int
    health_plan_id: int
    taxable_income_clp: str
    total_discount_clp: str
    pension: PensionContributionRead
    health: HealthContributionRead
    unemployment: UnemploymentContributionRead


class ComputeIncomeTaxRequest(BaseModel):
    """Represent Compute Income Tax Request."""

    utm_value_clp: Decimal | None = None


class ComputeIncomeTaxResponse(BaseModel):
    """Represent Compute Income Tax Response."""

    period_id: int
    taxable_income_clp: str
    deductible_amount_clp: str
    taxable_base_clp: str
    utm_value_clp: str
    taxable_base_utm: str
    bracket_lower_bound_utm: str
    bracket_upper_bound_utm: str | None
    marginal_rate: str
    rebate_utm: str
    tax_utm: str
    tax_clp: str


class DeflateAmountsRequest(BaseModel):
    """Represent Deflate Amounts Request."""

    target_year: int
    target_month: int
    index_code: str = "IPC_CL"


class DeflatedAmountRead(BaseModel):
    """Represent Deflated Amount Read."""

    nominal_clp: str
    real_clp: str


class DeflateAmountsResponse(BaseModel):
    """Represent Deflate Amounts Response."""

    period_id: int
    index_code: str
    source_year: int
    source_month: int
    target_year: int
    target_month: int
    source_index_value: str
    target_index_value: str
    taxable_income: DeflatedAmountRead
    gross_income: DeflatedAmountRead
    total_discounts: DeflatedAmountRead
    net_pay: DeflatedAmountRead


class PayrollItemDetailRead(BaseModel):
    """Represent Payroll Item Detail Read."""

    concept_code: str
    concept_name: str
    kind: str
    is_taxable: bool
    amount_clp: str
    notes: str | None


class PayrollSummaryRead(BaseModel):
    """Represent Payroll Summary Read."""

    period_id: int
    employer_id: int
    employer_name: str
    period_year: int
    period_month: int
    payment_date: date
    taxable_income_clp: str
    gross_income_clp: str
    total_discounts_clp: str
    net_pay_clp: str


@dataclass(frozen=True, slots=True)
class PayrollPeriodRangeRead(PayrollPeriodRangeFields):
    """Represent Payroll Period Range Read."""

    net_pay_clp: str | None
    position: Literal["previous", "current", "future"]
    increase: bool | None


@dataclass(frozen=True, slots=True)
class PayrollPeriodDetailRead(PayrollPeriodDetailFields):
    """Represent Payroll Period Detail Read."""

    status: str
    employment_contract_kind: str
    pension_plan_id: int | None
    health_plan_id: int | None
    items: list[PayrollItemDetailRead]
    summary: PayrollSummaryRead | None
    health_institution_is_active: bool | None = None


def to_payroll_summary_read(summary: PayrollSummaryDTO) -> PayrollSummaryRead:
    """Convert to payroll summary read."""
    return PayrollSummaryRead(
        period_id=summary.period_id,
        employer_id=summary.employer_id,
        employer_name=summary.employer_name,
        period_year=summary.period_year,
        period_month=summary.period_month,
        payment_date=summary.payment_date,
        taxable_income_clp=str(summary.taxable_income_clp),
        gross_income_clp=str(summary.gross_income_clp),
        total_discounts_clp=str(summary.total_discounts_clp),
        net_pay_clp=str(summary.net_pay_clp),
    )


def _compute_increase(
    item: PayrollPeriodRangeDTO,
    predecessor: PayrollPeriodRangeDTO | None,
) -> bool | None:
    """Return whether salary increased relative to the preceding period.

    Compares (salary_base / worked_days) * 30 for both periods.
    Returns None when data is insufficient to determine the direction.
    """
    if (
        predecessor is None
        or item.salary_base is None
        or not item.worked_days
        or predecessor.salary_base is None
        or not predecessor.worked_days
    ):
        return None
    current_normalized = (item.salary_base / item.worked_days) * 30
    prev_normalized = (predecessor.salary_base / predecessor.worked_days) * 30
    return current_normalized > prev_normalized


def to_payroll_period_range_reads(
    period_ranges: list[PayrollPeriodRangeDTO],
) -> list[PayrollPeriodRangeRead]:
    """Convert payroll period ranges to API reads with relative positions."""
    current_index = next(
        (index for index, item in enumerate(period_ranges) if item.is_current),
        None,
    )
    ranges: list[PayrollPeriodRangeRead] = []
    for index, item in enumerate(period_ranges):
        if item.is_lookback:
            continue  # ghost predecessor — not emitted, used only via index lookup
        position: Literal["previous", "current", "future"] = (
            "current"
            if item.is_current
            else "previous"
            if current_index is not None and index < current_index
            else "future"
        )
        if position in {"previous", "current"}:
            increase: bool | None = _compute_increase(
                item, period_ranges[index - 1] if index > 0 else None
            )
        else:
            increase = bool(item.increase)
        ranges.append(
            PayrollPeriodRangeRead(
                period_year=item.period_year,
                period_month=item.period_month,
                start_date=item.start_date,
                end_date=item.end_date,
                net_pay_clp=(
                    str(item.net_pay_clp) if item.net_pay_clp is not None else None
                ),
                position=position,
                increase=increase,
            )
        )
    return ranges


def to_deflated_amount_read(amount: DeflatedAmountDTO) -> DeflatedAmountRead:
    """Convert to deflated amount read."""
    return DeflatedAmountRead(
        nominal_clp=str(amount.nominal_clp), real_clp=str(amount.real_clp)
    )


def to_pdf_response(report: GeneratedPayrollReportDTO) -> Response:
    """Convert to pdf response."""
    return Response(
        content=report.content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report.filename}"'},
    )


@router.post("/import", response_model=ImportPayrollResponse)
async def import_payroll(
    file: UploadFile = File(...),
    use_case: ImportPayroll = Depends(get_import_payroll_use_case),
    process_use_case: ProcessImportedPayrollPeriods = Depends(
        get_process_imported_payroll_periods_use_case
    ),
) -> ImportPayrollResponse:
    """Import payroll."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="A payroll file name is required.")

    try:
        result = await use_case.from_bytes(file.filename, await file.read())
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc
    try:
        result = await process_use_case.execute(result)
    except PayrollError as exc:
        raise to_http_exception(exc) from exc

    return ImportPayrollResponse(
        imported_periods=result.imported_periods,
        imported_items=result.imported_items,
        periods=[
            to_imported_period_read(period, mode="commit") for period in result.periods
        ],
    )


@router.post("/import/rows", response_model=ImportPayrollResponse)
async def import_payroll_rows(
    payload: ImportPayrollRowsRequest,
    scope: TransactionalSessionScope = Depends(get_transactional_session),
    use_case: ImportPayroll = Depends(get_import_payroll_use_case_for_rows_import),
    process_use_case: ProcessImportedPayrollPeriods = Depends(
        get_process_imported_payroll_periods_use_case_for_rows_import
    ),
) -> ImportPayrollResponse:
    """Confirm already-structured payroll rows (e.g. from a PDF preview).

    Reuses the exact same pipeline as POST /payroll/import
    (ImportPayroll.from_rows() + ProcessImportedPayrollPeriods) against a
    TransactionalSessionScope: mode="commit" makes every write durable,
    mode="validate" runs the same computations then discards all of them.

    Rows with an unresolved concept_code are split out before either use
    case runs: mode="commit" rejects the whole request outright (400) if any
    remain, while mode="validate" simply excludes them from the computed
    pipeline and reports them back via `unresolved_rows` -- letting a caller
    iterate (fix a few rows, validate again) without a hard failure each
    time. See ImportPayrollRowsRequest's docstring for the full contract.

    One try/except around both steps (unlike /payroll/import's two separate
    blocks) on purpose: any failure here must resolve the scope to
    "validate" before re-raising, regardless of the requested mode -- a
    half-applied import must never be left committed.
    """
    unresolved = [
        UnresolvedRowWarning(
            row_index=index,
            amount_clp=str(row.amount_clp),
        )
        for index, row in enumerate(payload.rows)
        if row.concept_code is None
    ]

    try:
        if unresolved and payload.mode == "commit":
            raise PayrollValidationError(
                "Cannot commit: row(s) at index "
                f"{[item.row_index for item in unresolved]} have no resolved "
                'concept_code. Resend with mode="validate" to preview the '
                "rest, or resolve them first."
            )

        rows = [
            ImportPayrollRowDTO(
                employer=payload.employer,
                period_year=payload.period_year,
                period_month=payload.period_month,
                payment_date=payload.payment_date,
                status=resolve_declared_status(payload.declared_net_pay_clp),
                employment_contract_kind=payload.employment_contract_kind,
                concept_code=row.concept_code,
                amount_clp=row.amount_clp,
                worked_days=payload.worked_days,
                declared_net_pay_clp=payload.declared_net_pay_clp,
            )
            for row in payload.rows
            if row.concept_code is not None
        ]

        if payload.rows and not rows:
            # Every submitted row lacks a concept_code (commit already
            # raised above, so we can only get here in mode="validate"):
            # nothing to persist yet, but this is not an error.
            result = ImportPayrollResultDTO(
                imported_periods=0, imported_items=0, periods=[]
            )
        else:
            # Either every row is resolved, or payload.rows was empty to
            # begin with -- in which case from_rows([])'s own "must not be
            # empty" guard raises, unchanged from before this feature.
            result = await use_case.from_rows(rows)
            result = await process_use_case.execute(result)
    except PayrollError as exc:
        await scope.resolve("validate")
        raise to_http_exception(exc, default_status=400) from exc

    await scope.resolve(payload.mode)

    return ImportPayrollResponse(
        imported_periods=result.imported_periods,
        imported_items=result.imported_items,
        periods=[
            to_imported_period_read(period, mode=payload.mode)
            for period in result.periods
        ],
        unresolved_rows=unresolved,
    )


def to_pdf_import_preview_response(
    preview: PdfImportPreviewDTO,
) -> PdfImportPreviewResponse:
    """Convert a PdfImportPreviewDTO to its API response shape."""
    return PdfImportPreviewResponse(
        employer=preview.employer,
        period_year=preview.period_year,
        period_month=preview.period_month,
        payment_date=preview.payment_date,
        worked_days=preview.worked_days,
        declared_net_pay_clp=(
            str(preview.declared_net_pay_clp)
            if preview.declared_net_pay_clp is not None
            else None
        ),
        employment_contract_kind=(
            preview.employment_contract_kind.value
            if preview.employment_contract_kind is not None
            else None
        ),
        template_id=preview.template_id,
        rows=[
            PdfImportPreviewRowRead(
                raw_label=row.raw_label,
                amount_clp=str(row.amount_clp),
                kind=row.kind,
                concept_code=row.concept_code,
                confidence=row.confidence,
            )
            for row in preview.rows
        ],
    )


@router.post("/import/pdf-preview", response_model=PdfImportPreviewResponse)
async def preview_pdf_import(
    file: UploadFile = File(...),
    use_case: PreviewPdfImport = Depends(get_preview_pdf_import_use_case),
) -> PdfImportPreviewResponse:
    """Preview payroll data extracted from a PDF.

    Read-only: never touches PayrollRepository nor
    ProcessImportedPayrollPeriods, and never persists anything. A PDF that
    matches no known template still returns 200 with unresolved rows instead
    of failing -- see PreviewPdfImport / TemplatePdfPayrollExtractor.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="A PDF file name is required.")

    try:
        preview = await use_case.execute(file.filename, await file.read())
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc

    return to_pdf_import_preview_response(preview)


@router.get("/summary", response_model=list[PayrollSummaryRead])
async def list_payroll_summaries(
    queries: PayrollQueries = Depends(get_payroll_queries),
) -> list[PayrollSummaryRead]:
    """List payroll summaries."""
    return [
        to_payroll_summary_read(item) for item in await queries.list_period_summaries()
    ]


@router.get("/period-range", response_model=list[PayrollPeriodRangeRead])
async def list_payroll_period_ranges(
    queries: PayrollQueries = Depends(get_payroll_queries),
) -> list[PayrollPeriodRangeRead]:
    """List payroll period date ranges around the current period."""
    return to_payroll_period_range_reads(await queries.list_period_ranges())


@router.get("/{period_id}", response_model=PayrollPeriodDetailRead)
async def get_payroll_period(
    period_id: int = Path(..., gt=0),
    queries: PayrollQueries = Depends(get_payroll_queries),
) -> PayrollPeriodDetailRead:
    """Get payroll period."""
    try:
        detail = await queries.get_period_detail(period_id)
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=404) from exc

    return PayrollPeriodDetailRead(
        id=detail.id,
        employer_id=detail.employer_id,
        employer_name=detail.employer_name,
        employer_tax_id=detail.employer_tax_id,
        employer_country_code=detail.employer_country_code,
        employer_started_at=detail.employer_started_at,
        employer_ended_at=detail.employer_ended_at,
        period_year=detail.period_year,
        period_month=detail.period_month,
        payment_date=detail.payment_date,
        worked_days=detail.worked_days,
        status=detail.status,
        employment_contract_kind=detail.employment_contract_kind.value,
        pension_plan_id=detail.pension_plan_id,
        health_plan_id=detail.health_plan_id,
        items=[
            PayrollItemDetailRead(
                concept_code=item.concept_code,
                concept_name=item.concept_name,
                kind=item.kind,
                is_taxable=item.is_taxable,
                amount_clp=str(item.amount_clp),
                notes=item.notes,
            )
            for item in detail.items
        ],
        summary=to_payroll_summary_read(detail.summary)
        if detail.summary is not None
        else None,
        health_institution_is_active=detail.health_institution_is_active,
    )


@router.get(
    "/{period_id}/report.pdf",
    responses={200: {"content": {"application/pdf": {}}}},
)
async def get_payroll_report(
    period_id: int = Path(..., gt=0),
    use_case: GeneratePayrollReport = Depends(get_generate_payroll_report_use_case),
) -> Response:
    """Get payroll report."""
    try:
        return to_pdf_response(await use_case.execute(period_id))
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc


@router.post("/{period_id}/assign-plans", response_model=AssignPlansResponse)
async def assign_plans(
    payload: AssignPlansRequest,
    period_id: int = Path(..., gt=0),
    use_case: AssignPlans = Depends(get_assign_plans_use_case),
) -> AssignPlansResponse:
    """Assign plans."""
    try:
        result = await use_case.execute(
            AssignPlansCommandDTO(
                period_id=period_id,
                pension_plan_id=payload.pension_plan_id,
                health_plan_id=payload.health_plan_id,
            )
        )
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc

    return AssignPlansResponse(
        period_id=result.period_id,
        payment_date=result.payment_date,
        pension_plan_id=result.pension_plan_id,
        health_plan_id=result.health_plan_id,
    )


@router.post("/{period_id}/review", response_model=ReviewPayrollPeriodResponse)
async def review_payroll_period(
    period_id: int = Path(..., gt=0),
    use_case: ReviewPayrollPeriod = Depends(get_review_payroll_period_use_case),
) -> ReviewPayrollPeriodResponse:
    """Review payroll period."""
    try:
        result = await use_case.execute(
            ReviewPayrollPeriodCommandDTO(period_id=period_id)
        )
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc

    return ReviewPayrollPeriodResponse(
        period_id=result.period_id,
        payment_date=result.payment_date,
        status=result.status,
    )


@router.post("/{period_id}/compute-tax", response_model=ComputeIncomeTaxResponse)
async def compute_income_tax(
    payload: ComputeIncomeTaxRequest,
    period_id: int = Path(..., gt=0),
    use_case: ComputeIncomeTax = Depends(get_compute_income_tax_use_case),
) -> ComputeIncomeTaxResponse:
    """Compute income tax."""
    try:
        result = await use_case.execute(
            ComputeIncomeTaxCommandDTO(
                period_id=period_id,
                utm_value_clp=payload.utm_value_clp,
            )
        )
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc

    return ComputeIncomeTaxResponse(
        period_id=result.period_id,
        taxable_income_clp=str(result.tax.taxable_income_clp),
        deductible_amount_clp=str(result.tax.deductible_amount_clp),
        taxable_base_clp=str(result.tax.taxable_base_clp),
        utm_value_clp=str(result.tax.utm_value_clp),
        taxable_base_utm=str(result.tax.taxable_base_utm),
        bracket_lower_bound_utm=str(result.tax.bracket_lower_bound_utm),
        bracket_upper_bound_utm=(
            str(result.tax.bracket_upper_bound_utm)
            if result.tax.bracket_upper_bound_utm is not None
            else None
        ),
        marginal_rate=str(result.tax.marginal_rate),
        rebate_utm=str(result.tax.rebate_utm),
        tax_utm=str(result.tax.tax_utm),
        tax_clp=str(result.tax.tax_clp),
    )


@router.post("/{period_id}/deflate", response_model=DeflateAmountsResponse)
async def deflate_amounts(
    payload: DeflateAmountsRequest,
    period_id: int = Path(..., gt=0),
    use_case: DeflateAmounts = Depends(get_deflate_amounts_use_case),
) -> DeflateAmountsResponse:
    """Deflate amounts."""
    try:
        result = await use_case.execute(
            DeflateAmountsCommandDTO(
                period_id=period_id,
                target_year=payload.target_year,
                target_month=payload.target_month,
                index_code=payload.index_code,
            )
        )
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc

    return DeflateAmountsResponse(
        period_id=result.period_id,
        index_code=result.index_code,
        source_year=result.source_year,
        source_month=result.source_month,
        target_year=result.target_year,
        target_month=result.target_month,
        source_index_value=str(result.source_index_value),
        target_index_value=str(result.target_index_value),
        taxable_income=to_deflated_amount_read(result.taxable_income),
        gross_income=to_deflated_amount_read(result.gross_income),
        total_discounts=to_deflated_amount_read(result.total_discounts),
        net_pay=to_deflated_amount_read(result.net_pay),
    )


@router.post(
    "/{period_id}/compute-contributions", response_model=ComputeContributionsResponse
)
async def compute_contributions(
    payload: ComputeContributionsRequest,
    period_id: int = Path(..., gt=0),
    use_case: ComputeContributions = Depends(get_compute_contributions_use_case),
) -> ComputeContributionsResponse:
    """Compute contributions."""
    try:
        result = await use_case.execute(
            ComputeContributionsCommandDTO(
                period_id=period_id,
                pension_plan_id=payload.pension_plan_id,
                health_plan_id=payload.health_plan_id,
                uf_value_clp=payload.uf_value_clp,
            )
        )
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc

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
        unemployment=UnemploymentContributionRead(
            contract_kind=result.unemployment.contract_kind.value,
            taxable_clp=str(result.unemployment.taxable_clp),
            cap_clp=str(result.unemployment.cap_clp),
            capped_base_clp=str(result.unemployment.capped_base_clp),
            employee_rate=str(result.unemployment.employee_rate),
            employee_amount_clp=str(result.unemployment.employee_amount_clp),
            employer_rate=str(result.unemployment.employer_rate),
            employer_amount_clp=str(result.unemployment.employer_amount_clp),
        ),
    )
