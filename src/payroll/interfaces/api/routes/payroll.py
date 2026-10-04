"""Payroll routes."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    Response,
    UploadFile,
)
from dataclasses import dataclass
from pydantic import BaseModel, PlainSerializer

from payroll.application.errors import (
    PayrollError,
    PayrollImportNotValidatedError,
    PayrollValidationError,
)

from payroll.application.services.import_reconciliation import (
    conflicting_reconciliation_periods,
    is_import_fully_validated,
    period_has_reconciliation_conflict,
)
from payroll.application.dto import (
    ImportedComplementaryInsuranceValidationDTO,
    ImportedContributionValidationDTO,
    ImportedPayrollPeriodDTO,
    ImportPayrollResultDTO,
    ImportPayrollRowDTO,
    PdfImportPreviewDTO,
    PayrollPeriodRangeContextDTO,
    PayrollPeriodRangeDTO,
)
from payroll.domain.quantizers import quantize_clp, quantize_percent
from payroll.interfaces.api.errors import to_http_exception
from payroll.interfaces.session import TransactionalSessionScope
from payroll.application.use_cases.payroll_queries import PayrollQueries
from payroll.interfaces.api.dependencies import (
    get_payroll_queries,
    get_preview_pdf_import_use_case,
    get_transactional_import_payroll_use_case,
    get_transactional_process_imported_payroll_periods_use_case,
    get_transactional_session,
    get_transactional_delete_payroll_periods_use_case,
)

if TYPE_CHECKING:
    from payroll.application.use_cases.preview_pdf_import import PreviewPdfImport
    from payroll.application.use_cases.import_payroll import ImportPayroll
    from payroll.application.use_cases.process_imported_payroll_periods import (
        ProcessImportedPayrollPeriods,
    )

router = APIRouter(prefix="/payroll", tags=["payroll"])

# MoneyCLP renders as a genuine JSON number (not a quoted string) for the
# handful of fields below, at the caller's explicit request. Everywhere else
# in this API, money/rate fields are deliberately plain `str` (see the many
# `str(result.foo_clp)` conversions throughout this file and
# reference_data.py): pydantic serializes bare `Decimal` fields as JSON
# strings by default specifically to avoid float64 precision loss on the
# wire, matching AGENTS.md's "never float" rule. That precision concern does
# not apply here at all, not even in theory: the Chilean peso (CLP) has no
# subunit/decimal denomination, so every value is already an exact whole
# number -- `to_integral_value()` rounds defensively (ROUND_HALF_EVEN) in
# case a NUMERIC column's cosmetic `.00` scale ever hid a stray fractional
# artifact, then converts to a real `int`, which JSON represents exactly.
# Scoped narrowly to ImportedPeriodRead/ImportedContributionValidationRead
# rather than applied API-wide -- see AGENTS.md on cross-cutting changes.
# Internal computation is untouched: every value here is still a real
# `Decimal` right up until this last-mile JSON serialization step.
MoneyCLP = Annotated[
    Decimal,
    PlainSerializer(
        lambda v: int(v.to_integral_value()), return_type=int, when_used="json"
    ),
]


class UnresolvedRowWarning(BaseModel):
    """Represent one submitted row whose concept_code could not be resolved.

    Surfaced by POST /payroll/import/json in mode="validate" so a caller can
    fix these specific rows before resending with mode="commit". Identified
    by `period_index` (the row's position in the top-level `periods` list)
    plus `row_index` (its position within that period's own `rows` list) --
    a plain `row_index` alone stopped being unambiguous once a single
    request could carry more than one period. No employer/period fields
    here -- those live once per period at ImportPayrollPeriodRequest, so
    repeating them per warning would just be duplicate information the
    caller already sent.
    """

    period_index: int
    row_index: int
    amount_clp: str


class ImportedContributionValidationRead(BaseModel):
    """Represent imported contribution validation results in an API response.

    A deliberate, jscpd-exempted mirror of ImportedContributionValidationDTO
    (same reasoning as ImportedPeriodRead's own docstring) -- with one
    intentional difference: every *_clp field uses MoneyCLP here instead of
    the DTO's bare Decimal, so these amounts render as real JSON numbers
    instead of the quoted strings pydantic emits for Decimal by default.
    """

    # jscpd:ignore-start
    declared_pension_base_clp: MoneyCLP | None = None
    expected_pension_base_clp: MoneyCLP | None = None
    pension_base_difference_clp: MoneyCLP | None = None
    declared_pension_additional_clp: MoneyCLP | None = None
    expected_pension_additional_clp: MoneyCLP | None = None
    pension_additional_difference_clp: MoneyCLP | None = None
    declared_health_base_clp: MoneyCLP | None = None
    expected_health_base_clp: MoneyCLP | None = None
    health_base_difference_clp: MoneyCLP | None = None
    declared_health_plan_additional_clp: MoneyCLP | None = None
    expected_health_plan_additional_clp: MoneyCLP | None = None
    health_plan_additional_difference_clp: MoneyCLP | None = None
    warning: str | None = None
    # jscpd:ignore-end


def to_imported_contribution_validation_read(
    validation: ImportedContributionValidationDTO | None,
) -> ImportedContributionValidationRead | None:
    """Convert an ImportedContributionValidationDTO to its API response shape."""
    if validation is None:
        return None
    return ImportedContributionValidationRead(
        declared_pension_base_clp=validation.declared_pension_base_clp,
        expected_pension_base_clp=validation.expected_pension_base_clp,
        pension_base_difference_clp=validation.pension_base_difference_clp,
        declared_pension_additional_clp=validation.declared_pension_additional_clp,
        expected_pension_additional_clp=validation.expected_pension_additional_clp,
        pension_additional_difference_clp=(
            validation.pension_additional_difference_clp
        ),
        declared_health_base_clp=validation.declared_health_base_clp,
        expected_health_base_clp=validation.expected_health_base_clp,
        health_base_difference_clp=validation.health_base_difference_clp,
        declared_health_plan_additional_clp=(
            validation.declared_health_plan_additional_clp
        ),
        expected_health_plan_additional_clp=(
            validation.expected_health_plan_additional_clp
        ),
        health_plan_additional_difference_clp=(
            validation.health_plan_additional_difference_clp
        ),
        warning=validation.warning,
    )


class ImportedPeriodRead(BaseModel):
    """Represent one imported payroll period in an import response.

    Mirrors ImportedPayrollPeriodDTO field-for-field, with two deliberate
    differences: `id` is `int | None` here, not `int`, and `validated` is
    declared first instead of last (see below for both). Both POST
    /payroll/import/spreadsheet and POST /payroll/import/json can run in
    mode="validate", where the INSERT genuinely happens against a SAVEPOINT
    (so contributions, tax and net-pay warnings are computed for real, not
    guessed) but is always rolled back before the response goes out -- see
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

    `validated` is declared first (ahead of even `id`), deliberately
    breaking the DTO mirror below -- pydantic v2 serializes fields in
    declaration order, so this puts the one field a caller scanning a
    large `periods[]` array actually cares about at a glance right at the
    start of each JSON object, instead of buried after every CLP amount.
    """

    validated: bool
    # jscpd:ignore-start
    id: int | None
    employer: str
    period_year: int
    period_month: int
    payment_date: date
    item_count: int
    worked_days: int = 30
    declared_net_pay_clp: MoneyCLP | None = None
    expected_net_pay_clp: MoneyCLP | None = None
    net_pay_difference_clp: MoneyCLP | None = None
    net_pay_warning: str | None = None
    contribution_validation: ImportedContributionValidationRead | None = None
    complementary_insurance_validation: (
        ImportedComplementaryInsuranceValidationDTO | None
    ) = None
    # jscpd:ignore-end


def to_imported_period_read(
    period: ImportedPayrollPeriodDTO,
    *,
    mode: Literal["commit", "validate"],
    has_unresolved: bool = False,
) -> ImportedPeriodRead:
    """Convert an ImportedPayrollPeriodDTO to its API response shape.

    Nulls out `id` when mode == "validate" -- see ImportedPeriodRead's
    docstring for why that id must never be treated as a real reference.
    `has_unresolved` defaults to False for POST /payroll/import/spreadsheet
    (a CSV/XLSX row always carries a resolved concept_code, so this never
    applies there) -- POST /payroll/import/json's route passes it in
    explicitly per period, since that's the only caller where a period can
    have rows still missing a concept_code (see `unresolved_rows` on
    ImportPayrollResponse). `validated` here mirrors the exact same
    "no *known* conflict" semantics as the top-level `validated` field on
    ImportPayrollResponse (see period_has_reconciliation_conflict() --
    "pending" reconciliation states are not conflicts), just scoped to one
    period instead of the whole batch -- so a caller can tell at a glance
    which specific period(s) in `periods` need attention instead of
    scanning every one by hand once the top-level `validated` is False.
    """
    return ImportedPeriodRead(
        validated=not has_unresolved and not period_has_reconciliation_conflict(period),
        id=period.id if mode == "commit" else None,
        employer=period.employer,
        period_year=period.period_year,
        period_month=period.period_month,
        payment_date=period.payment_date,
        item_count=period.item_count,
        worked_days=period.worked_days,
        declared_net_pay_clp=period.declared_net_pay_clp,
        expected_net_pay_clp=period.expected_net_pay_clp,
        net_pay_difference_clp=period.net_pay_difference_clp,
        net_pay_warning=period.net_pay_warning,
        contribution_validation=to_imported_contribution_validation_read(
            period.contribution_validation
        ),
        complementary_insurance_validation=period.complementary_insurance_validation,
    )


def build_reconciliation_conflict_detail(
    message: str, periods: list[ImportedPayrollPeriodDTO]
) -> dict[str, object]:
    """Build the structured 400 `detail` payload for a reconciliation conflict.

    Includes only the conflicting periods (see
    conflicting_reconciliation_periods()) -- a 22-period import with one bad
    period should not force the caller to scan 21 clean ones to find it.
    Reuses to_imported_period_read(mode="validate") so each entry has the
    exact same shape a caller already knows how to parse from a normal
    response: `id=None` for the same reason as any other validate-mode
    period -- the whole transaction was rolled back once a genuine conflict
    was found, so no id from it is real (see ImportedPeriodRead).
    """
    return {
        "message": message,
        "conflicting_periods": [
            to_imported_period_read(period, mode="validate").model_dump(mode="json")
            for period in conflicting_reconciliation_periods(periods)
        ],
    }


class ImportPayrollResponse(BaseModel):
    """Represent Import Payroll Response.

    `mode` echoes back what actually happened, because `period_count` /
    `item_count` mean two different things depending on it:
    mode="commit" -> both counts describe rows genuinely persisted.
    mode="validate" -> both counts describe what *would* be persisted if the
    same payload were resent with mode="commit" -- nothing was written (see
    TransactionalSessionScope / ImportedPeriodRead's docstring for why
    `periods[].id` is also null in that case). Both POST /payroll/import/spreadsheet
    and POST /payroll/import/json support mode="validate"; it defaults to
    "commit" on both so existing callers see no behavior change.

    Named `period_count`/`item_count` rather than `periods`/`items` on
    purpose -- `periods` below is already the actual list of period
    objects, so a same-named count field would collide with it.

    `validated` and `saved` both describe the *returned* result, not a
    promise about what was attempted -- a genuine declared-vs-computed
    conflict (see _period_has_reconciliation_conflict()) makes the whole
    request fail with a 422 (PayrollImportNotValidatedError) instead of
    reaching this response, in *either* mode -- see its own docstring for
    why 422 rather than the plain 400 most other business-rule violations
    in this codebase use. So a 200 response, in either mode,
    always has `validated=True`. "Pending" reconciliation states (a
    projected period awaiting plan assignment, a temporary market-data gap)
    do *not* count as conflicts and never block either mode -- there's
    nothing wrong, just nothing to compare yet, so `validated=True` there
    does not mean "fully reconciled", only "no known conflict".
    - `validated`: True when every row got a resolved concept_code and no
      period has a genuine net-pay/contribution/complementary-insurance
      conflict. See is_import_fully_validated(). Always True by the time a
      200 is reached (see above) -- kept as an explicit field rather than
      dropped, so a caller doesn't have to infer it from the HTTP status
      alone, and because periods[].validated (below) still varies even when
      this top-level field can't.
    - `saved`: True once the write is durable (mode="commit"), `None` when
      mode="validate" -- nothing was persisted, so "was it saved" does not
      apply. **Never False** -- not merely unlikely, but structurally
      unreachable: `saved=False` would mean "we tried to commit and it did
      not persist", but that exact scenario -- an unvalidated commit -- is
      precisely what the 422 above rejects before this response is ever
      built. There is no code path that returns 200 with `saved=False`; do
      not write client code that branches on it.

    validated_period_count plus unvalidated_period_count are a quick
    summary over periods[].validated (they always sum to len(periods)) so a
    caller with a large batch does not have to scan every entry just to
    know how many are actually fine -- each periods[] entry's own
    validated field (see to_imported_period_read()) then says which one(s)
    need a closer look. Same no-known-conflict semantics as the top-level
    validated field above, just counted per period instead of aggregated
    across the whole batch.
    """

    mode: Literal["commit", "validate"]
    validated: bool
    saved: bool | None
    period_count: int
    item_count: int
    validated_period_count: int
    unvalidated_period_count: int
    periods: list[ImportedPeriodRead]
    unresolved_rows: list[UnresolvedRowWarning] = []


class ImportPayrollRowRequest(BaseModel):
    """Represent a single already-structured payroll concept to persist.

    Deliberately just concept_code + amount_clp: employer, period_year,
    period_month, payment_date, worked_days, and
    declared_net_pay_clp all live once per period at
    ImportPayrollPeriodRequest's top level instead of being repeated per
    row -- every row submitted through this endpoint comes from the same
    single payslip (see
    PdfImportPreviewResponse -- one element of the array POST
    /payroll/pdf-preview returns -- which has the identical shape: one set
    of header fields, N rows each carrying only their own concept-level
    data).
    concept_code is optional here (unlike ImportPayrollRowDTO, where it is
    required) so that a row a human hasn't finished resolving yet can still
    be submitted with mode="validate" -- see ImportPayrollPeriodRequest below.
    """

    concept_code: str | None
    amount_clp: Decimal


class ImportPayrollPeriodRequest(BaseModel):
    """Represent one payslip/period block inside a JSON import request."""

    period_id: int | None = None
    employer: str
    period_year: int
    period_month: int
    payment_date: date
    worked_days: int = 30
    declared_net_pay_clp: Decimal | None = None
    rows: list[ImportPayrollRowRequest]


class DeletePayrollPeriodsRequest(BaseModel):
    """Represent an atomic bulk payroll-period deletion request."""

    period_ids: list[int]


class ImportPayrollJsonRequest(BaseModel):
    """Represent the request body for POST /payroll/import/json."""

    mode: Literal["commit", "validate"] = "commit"
    periods: list[ImportPayrollPeriodRequest]


class PdfImportPreviewRowRead(BaseModel):
    """Represent a single candidate row in a PDF import preview response."""

    raw_label: str
    amount_clp: str
    kind: str
    concept_code: str | None
    confidence: float


class PdfImportPreviewResponse(BaseModel):
    """Represent one payslip's preview in the POST /payroll/pdf-preview response.

    POST /payroll/pdf-preview returns a list of these -- one per uploaded PDF,
    in upload order -- since that endpoint now accepts a batch of payslips in
    a single request. Never persists anything -- see PreviewPdfImport /
    TemplatePdfPayrollExtractor. Each element is deliberately shaped so it
    can be used verbatim as one entry of a POST /payroll/import/json
    request's `periods` list. The contract is resolved from the employer's
    effective employment-contract record; this preview never accepts or
    exposes contract kind.
    """

    employer: str | None
    period_year: int | None
    period_month: int | None
    payment_date: date | None
    worked_days: int | None
    declared_net_pay_clp: str | None
    template_id: str | None

    rows: list[PdfImportPreviewRowRead]


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


@dataclass(frozen=True, slots=True)
class PayrollPeriodEmployerRead:
    """Represent the nested employer object on a PayrollPeriodRead.

    A proper nested object rather than a flat employer_id -- room to grow
    (e.g. tax_id, country_code) without another breaking reshape of
    PayrollPeriodRead later. None (the whole object, not its fields) on
    PayrollPeriodRead for any synthetic entry with no real backing row.
    """

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class PayrollPeriodDateRangeRead:
    """Represent the nested start/end date range on a PayrollPeriodInfoRead."""

    start: date
    end: date


@dataclass(frozen=True, slots=True)
class PayrollPeriodInfoRead:
    """Represent the nested temporal identity of a PayrollPeriodRead.

    Groups year/month/date_range/timeframe -- everything that answers "when
    is this period, and where does it sit relative to today" -- under one
    key, mirroring `employer` (relationship) and `amount` (financial
    breakdown) as the three value-object buckets making up PayrollPeriodRead.

    `timeframe` deliberately uses "future", not "next": this field answers
    "what temporal *direction* is this period relative to today", a
    category that -- exactly like "previous" -- can describe many periods at
    once (list_period_ranges() returns up to 12 of each by default), not a
    single immediately-adjacent one the way "next" would imply.
    """

    year: int
    month: int
    date_range: PayrollPeriodDateRangeRead
    timeframe: Literal["previous", "current", "future"]


@dataclass(frozen=True, slots=True)
class PayrollPeriodAmountRead:
    """Represent the nested financial breakdown of a PayrollPeriodRead.

    gross_income_clp/taxable_income_clp/total_discounts_clp are None for
    synthetic entries that have no real backing DB row -- inferred (padded)
    previous periods and every future (always-projected) period -- same
    honesty-over-fabrication philosophy as every other field here.
    """

    gross_income_clp: int | None
    taxable_income_clp: int | None
    total_discounts_clp: int | None
    net_pay_clp: int | None
    net_pay_uf: float | None = None
    net_pay_usd: float | None = None
    net_pay_eur: float | None = None
    increase: float | None = None
    net_pay_clp_today: int | None = None


@dataclass(frozen=True, slots=True)
class PayrollPeriodRead:
    """Represent the unified payroll period read.

    Shared verbatim by GET /payroll (a list of these) and GET
    /payroll/{period_id} (one of these) -- replaces the previously
    diverging PayrollPeriodRangeRead/PayrollSummaryRead/
    PayrollPeriodDetailRead shapes those three endpoints used to return.
    `id`/`employer` are None for synthetic entries that have no real
    backing DB row -- inferred (padded) previous periods and every future
    (always-projected) period -- same honesty-over-fabrication philosophy
    as every other field here. `period` and `amount` are never None
    themselves (only their inner fields are) -- see PayrollPeriodInfoRead
    and PayrollPeriodAmountRead.
    """

    id: int | None
    employer: PayrollPeriodEmployerRead | None
    period: PayrollPeriodInfoRead
    amount: PayrollPeriodAmountRead


def _compute_increase(
    item: PayrollPeriodRangeDTO,
    predecessor: PayrollPeriodRangeDTO | None,
) -> Decimal | None:
    """Return the percentage variation of salary_base vs the preceding period.

    Compares (salary_base / worked_days) * 30 for both periods -- the same
    worked-days normalization this comparison has always used -- and
    expresses the change as a percentage, quantized to 2 decimals. Returns
    None when data is insufficient to compute a meaningful percentage: no
    predecessor, missing salary_base/worked_days on either side, or a
    zero-salary predecessor baseline (percent change from zero is
    undefined).
    """
    if (
        predecessor is None
        or item.salary_base is None
        or not item.worked_days
        or predecessor.salary_base is None
        or not predecessor.worked_days
    ):
        return None
    current_normalized = (item.salary_base / item.worked_days) * Decimal(30)
    prev_normalized = (predecessor.salary_base / predecessor.worked_days) * Decimal(30)
    if prev_normalized == 0:
        return None
    return quantize_percent(
        (current_normalized - prev_normalized) / prev_normalized * Decimal(100)
    )


def _compute_net_pay_clp_today(
    item: PayrollPeriodRangeDTO,
    current: PayrollPeriodRangeDTO | None,
) -> Decimal | None:
    """Reprice a `previous` period's net_pay_clp using today's real drivers.

    Splits `item`'s historical net_pay into the same two components
    `PredictedNetPayBaseline` already uses for future projections --
    `scalable_clp` (salary_base-driven: `net_pay_clp + fixed_uf_clp`) and
    `fixed_uf_clp` (the UF/CLP-exchange-rate-driven `HEALTH_ADDITIONAL_UF`
    discount) -- and reprices each with its *own* real driver instead of a
    single blanket UF ratio applied to the whole net figure:

    - `scalable_clp` is scaled by the real salary_base growth between
      `item` and `current` (the same `(salary_base / worked_days) * 30`
      normalization `_compute_increase()` already uses) -- this reflects
      *this employee's own recorded raises*, not a general inflation
      proxy.
    - `fixed_uf_clp` is repriced by holding its UF quantity constant and
      applying today's UF/CLP rate -- it is genuinely UF-denominated (an
      Isapre plan top-up), so this is exact, not an approximation.

    Both `item`'s own and today's UF/CLP rate are derived purely from
    already-resolved DTO fields (`net_pay_clp / net_pay_uf`, per period --
    see resolve_currency_equivalents()), so no extra `pf-rates` lookup is
    needed here, same as before this split.

    None whenever any ingredient is missing: no `current` period, either
    side's `net_pay_clp`/`net_pay_uf`/`salary_base`/`worked_days` didn't
    resolve, either side's own UF rate or normalized salary_base would
    require dividing by zero -- same independent-degradation philosophy as
    every other derived field in this endpoint.
    """
    if (
        current is None
        or item.net_pay_clp is None
        or item.net_pay_uf is None
        or item.net_pay_uf == 0
        or item.salary_base is None
        or not item.worked_days
        or current.net_pay_clp is None
        or current.net_pay_uf is None
        or current.net_pay_uf == 0
        or current.salary_base is None
        or not current.worked_days
    ):
        return None

    item_normalized_salary = (item.salary_base / item.worked_days) * Decimal(30)
    if item_normalized_salary == 0:
        return None
    current_normalized_salary = (current.salary_base / current.worked_days) * Decimal(
        30
    )
    salary_base_ratio = current_normalized_salary / item_normalized_salary

    scalable_historical = item.net_pay_clp + item.fixed_uf_clp
    scalable_today = scalable_historical * salary_base_ratio

    item_uf_rate = item.net_pay_clp / item.net_pay_uf
    today_uf_rate = current.net_pay_clp / current.net_pay_uf
    fixed_uf_quantity = item.fixed_uf_clp / item_uf_rate
    fixed_uf_today = fixed_uf_quantity * today_uf_rate

    return quantize_clp(scalable_today - fixed_uf_today)


def _to_money_int(value: Decimal | None) -> int | None:
    """Quantize a CLP Decimal amount to the nearest peso, as an int.

    Shared by every CLP-denominated field in PayrollPeriodRead (net_pay_clp,
    net_pay_clp_today, and now gross_income_clp/taxable_income_clp/
    total_discounts_clp too) so all of them use one consistent whole-peso
    representation -- deliberately not the `str`-preserving-cents
    representation the now-removed PayrollSummaryRead used to use for the
    same three summary fields.
    """
    return int(quantize_clp(value)) if value is not None else None


def _to_optional_float(value: Decimal | None) -> float | None:
    """Convert an optional Decimal to an optional float."""
    return float(value) if value is not None else None


def _build_employer_read(
    item: PayrollPeriodRangeDTO,
) -> PayrollPeriodEmployerRead | None:
    """Build the nested employer object, or None for a synthetic entry.

    Both employer_id and employer_name come from the same real DB row --
    either both are present or neither is (never a half-built employer).
    """
    if item.employer_id is None or item.employer_name is None:
        return None
    return PayrollPeriodEmployerRead(id=item.employer_id, name=item.employer_name)


def _build_payroll_period_read(
    item: PayrollPeriodRangeDTO,
    *,
    timeframe: Literal["previous", "current", "future"],
    increase: Decimal | None,
    net_pay_clp_today: Decimal | None,
) -> PayrollPeriodRead:
    """Build one PayrollPeriodRead from a DTO plus its derived fields.

    Shared by to_payroll_period_reads() (the GET /payroll list) and
    to_payroll_period_read() (the GET /payroll/{period_id} single item) so
    the two endpoints can never describe the same period differently.
    """
    return PayrollPeriodRead(
        id=item.period_id,
        employer=_build_employer_read(item),
        period=PayrollPeriodInfoRead(
            year=item.period_year,
            month=item.period_month,
            date_range=PayrollPeriodDateRangeRead(
                start=item.start_date, end=item.end_date
            ),
            timeframe=timeframe,
        ),
        amount=PayrollPeriodAmountRead(
            gross_income_clp=_to_money_int(item.gross_income_clp),
            taxable_income_clp=_to_money_int(item.taxable_income_clp),
            total_discounts_clp=_to_money_int(item.total_discounts_clp),
            net_pay_clp=_to_money_int(item.net_pay_clp),
            net_pay_uf=_to_optional_float(item.net_pay_uf),
            net_pay_usd=_to_optional_float(item.net_pay_usd),
            net_pay_eur=_to_optional_float(item.net_pay_eur),
            increase=_to_optional_float(increase),
            net_pay_clp_today=_to_money_int(net_pay_clp_today),
        ),
    )


def to_payroll_period_reads(
    period_ranges: list[PayrollPeriodRangeDTO],
) -> list[PayrollPeriodRead]:
    """Convert payroll period ranges to API reads with relative timeframes."""
    current_index = next(
        (index for index, item in enumerate(period_ranges) if item.is_current),
        None,
    )
    current_item = period_ranges[current_index] if current_index is not None else None
    ranges: list[PayrollPeriodRead] = []
    for index, item in enumerate(period_ranges):
        if item.is_lookback:
            continue  # ghost predecessor — not emitted, used only via index lookup
        timeframe: Literal["previous", "current", "future"] = (
            "current"
            if item.is_current
            else "previous"
            if current_index is not None and index < current_index
            else "future"
        )
        if timeframe in {"previous", "current"}:
            increase: Decimal | None = _compute_increase(
                item, period_ranges[index - 1] if index > 0 else None
            )
        else:
            increase = item.increase
        net_pay_clp_today = (
            _compute_net_pay_clp_today(item, current_item)
            if timeframe == "previous"
            else None
        )
        ranges.append(
            _build_payroll_period_read(
                item,
                timeframe=timeframe,
                increase=increase,
                net_pay_clp_today=net_pay_clp_today,
            )
        )
    return ranges


def _resolve_single_timeframe(
    target: PayrollPeriodRangeDTO,
    current: PayrollPeriodRangeDTO | None,
) -> Literal["previous", "current", "future"]:
    """Resolve a single real period's timeframe relative to today's current.

    Unlike to_payroll_period_reads()'s index-into-a-list approach (timeframe
    is relative to where the item sits in an already-ordered window), this
    compares target directly against the independently-resolved `current`
    DTO -- get_period_range() has no window/list, just the one period.
    Falls back to "previous" in the rare edge case where no period
    anywhere qualifies as "current" yet (a fresh system with only
    not-yet-declared periods) -- same "never crash, degrade to the most
    conservative honest answer" philosophy used throughout this endpoint.
    """
    if current is not None and target.period_id == current.period_id:
        return "current"
    if current is None or target.start_date < current.start_date:
        return "previous"
    return "future"


def to_payroll_period_read(context: PayrollPeriodRangeContextDTO) -> PayrollPeriodRead:
    """Convert a single period range context to the unified API read.

    Backs GET /payroll/{period_id} -- same field shape and same
    increase/net_pay_clp_today derivation helpers as to_payroll_period_reads(),
    just applied to one period plus its own predecessor/current context
    instead of a whole window.
    """
    timeframe = _resolve_single_timeframe(context.target, context.current)
    increase = _compute_increase(context.target, context.predecessor)
    net_pay_clp_today = (
        _compute_net_pay_clp_today(context.target, context.current)
        if timeframe == "previous"
        else None
    )
    return _build_payroll_period_read(
        context.target,
        timeframe=timeframe,
        increase=increase,
        net_pay_clp_today=net_pay_clp_today,
    )


def count_validated_periods(periods_read: list[ImportedPeriodRead]) -> tuple[int, int]:
    """Split a periods_read list into (validated_count, unvalidated_count).

    Shared by both POST /payroll/import/spreadsheet and POST
    /payroll/import/json so ImportPayrollResponse's validated_period_count /
    unvalidated_period_count fields are computed identically by both --
    see ImportPayrollResponse's own docstring for what these two counts mean.
    """
    validated_period_count = sum(1 for period in periods_read if period.validated)
    return validated_period_count, len(periods_read) - validated_period_count


@router.post("/import/spreadsheet", response_model=ImportPayrollResponse)
async def import_payroll(
    file: UploadFile = File(...),
    mode: Literal["commit", "validate"] = Form("commit"),
    scope: TransactionalSessionScope = Depends(get_transactional_session),
    use_case: ImportPayroll = Depends(get_transactional_import_payroll_use_case),
    process_use_case: ProcessImportedPayrollPeriods = Depends(
        get_transactional_process_imported_payroll_periods_use_case
    ),
) -> ImportPayrollResponse:
    """Import payroll.

    Runs on the same transactional-scope machinery as POST
    /payroll/import/json: the whole import + reconciliation pipeline runs
    inside one SAVEPOINT. mode="commit" (the default, unchanged behavior for
    existing callers) makes the result durable once
    is_import_fully_validated() confirms no genuine declared-vs-computed
    conflict was found -- a conflict rolls everything back and fails the
    request instead of persisting partially-reconciled data.
    mode="validate" runs the exact same pipeline (so every warning is
    genuine, not simulated) and always rolls back regardless of the result,
    letting a caller preview an entire CSV/XLSX file's conflicts before
    ever touching the database -- sent as a `mode` form field alongside
    `file`, not JSON, since this is a multipart/form-data upload. Raises a
    422 (PayrollImportNotValidatedError) if the result is not fully
    validated, in *either* mode -- see PayrollImportNotValidatedError's
    docstring for why 422 instead of a plain 400. See
    ImportPayrollResponse's docstring for the full contract, shared with
    POST /payroll/import/json.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="A payroll file name is required.")

    try:
        result = await use_case.from_bytes(file.filename, await file.read())
        result = await process_use_case.execute(result)
        validated = is_import_fully_validated(result.periods)
        if not validated:
            if mode == "commit":
                message = (
                    "Cannot commit: computed contributions/net pay do not match "
                    "the declared amounts for one or more periods. Resend with "
                    'mode="validate" to inspect the warnings, or fix the '
                    "underlying file or reference data first."
                )
            else:
                message = (
                    "Import validation failed: computed contributions/net pay "
                    "do not match the declared amounts for one or more "
                    "periods. See conflicting_periods in this error's detail "
                    "for specifics."
                )
            raise PayrollImportNotValidatedError(
                message,
                detail=build_reconciliation_conflict_detail(message, result.periods),
            )
        periods_read = [
            to_imported_period_read(period, mode=mode) for period in result.periods
        ]
    except PayrollError as exc:
        await scope.resolve("validate")
        raise to_http_exception(exc, default_status=400) from exc

    await scope.resolve(mode)

    validated_period_count, unvalidated_period_count = count_validated_periods(
        periods_read
    )
    return ImportPayrollResponse(
        mode=mode,
        validated=validated,
        saved=True if mode == "commit" else None,
        period_count=result.imported_periods,
        item_count=result.imported_items,
        validated_period_count=validated_period_count,
        unvalidated_period_count=unvalidated_period_count,
        periods=periods_read,
    )


def _reject_duplicate_period_keys(periods: list[ImportPayrollPeriodRequest]) -> None:
    """Reject a `periods` list where two blocks share the same period key.

    Each block owns its own header fields (payment_date, contract kind,
    declared_net_pay_clp) independently of any other -- silently merging two
    entries with the same (employer, period_year, period_month) would mean
    picking one entry's header over the other's with no signal to the
    caller. /payroll/import/spreadsheet's row-level grouping never has this
    problem: a CSV/XLSX row has no separate "period header" to conflict.
    Raise PayrollValidationError (not a bare pydantic validator) to match
    this endpoint's existing 400-for-business-rule-violations convention
    (see e.g. the unresolved-concept_code commit check just below).
    """
    seen: set[tuple[str, int, int]] = set()
    duplicates: set[tuple[str, int, int]] = set()
    for period in periods:
        key = (period.employer, period.period_year, period.period_month)
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    if duplicates:
        raise PayrollValidationError(
            "periods must not repeat the same (employer, period_year, "
            f"period_month) combination: {sorted(duplicates)}"
        )


def _validate_period_ids(periods: list[ImportPayrollPeriodRequest]) -> None:
    """Validate explicit update identities before persistence begins."""
    ids = [period.period_id for period in periods if period.period_id is not None]
    if any(period_id <= 0 for period_id in ids):
        raise PayrollValidationError("period_id must be positive.")
    if len(ids) != len(set(ids)):
        raise PayrollValidationError("period_id values must not be duplicated.")


@router.post("/import/json", response_model=ImportPayrollResponse)
async def import_payroll_rows(
    payload: ImportPayrollJsonRequest,
    scope: TransactionalSessionScope = Depends(get_transactional_session),
    use_case: ImportPayroll = Depends(get_transactional_import_payroll_use_case),
    process_use_case: ProcessImportedPayrollPeriods = Depends(
        get_transactional_process_imported_payroll_periods_use_case
    ),
) -> ImportPayrollResponse:
    """Confirm one or more already-structured payroll periods (e.g. from a PDF preview).

    Reuses the exact same pipeline as POST /payroll/import/spreadsheet
    (ImportPayroll.from_rows() + ProcessImportedPayrollPeriods) against a
    TransactionalSessionScope: mode="commit" makes every write durable,
    mode="validate" runs the same computations then discards all of them.
    `mode` applies once to the whole `periods` batch; there is no per-period
    mode. `periods` must have at least one element, and no two elements may
    share the same (employer, period_year, period_month) -- see
    _reject_duplicate_period_keys().

    Rows with an unresolved concept_code are split out before either use
    case runs: mode="commit" rejects the whole request outright (422,
    PayrollImportNotValidatedError) if any remain, while mode="validate"
    excludes them from the computed pipeline so the rest of the batch still
    gets genuine warnings, then raises the same 422 reporting them back via
    `unresolved_rows` in the error detail (each entry identified by
    `period_index` + `row_index`) -- a caller iterates by inspecting that
    detail, fixing a few rows, and resending.
    mode="commit" also rejects (422, nothing persisted) if the pipeline
    finds a genuine declared-vs-computed conflict once it runs, and
    mode="validate" raises the same 422 as above for that case too -- see
    is_import_fully_validated(), PayrollImportNotValidatedError's docstring,
    and ImportPayrollResponse's docstring for why a 200 response (in either
    mode) always means `validated=True`.
    See ImportPayrollJsonRequest's docstring for the full contract.

    One try/except around every step (unlike /payroll/import/spreadsheet's two separate
    blocks) on purpose: any failure here must resolve the scope to
    "validate" before re-raising, regardless of the requested mode -- a
    half-applied import must never be left committed.
    """
    unresolved = [
        UnresolvedRowWarning(
            period_index=period_index,
            row_index=row_index,
            amount_clp=str(row.amount_clp),
        )
        for period_index, period in enumerate(payload.periods)
        for row_index, row in enumerate(period.rows)
        if row.concept_code is None
    ]

    try:
        if not payload.periods:
            raise PayrollValidationError("The periods list must not be empty.")
        _validate_period_ids(payload.periods)
        _reject_duplicate_period_keys(payload.periods)

        if unresolved and payload.mode == "commit":
            message = (
                "Cannot commit: row(s) at (period_index, row_index) "
                f"{[(item.period_index, item.row_index) for item in unresolved]} "
                'have no resolved concept_code. Resend with mode="validate" to '
                "preview the rest, or resolve them first."
            )
            raise PayrollImportNotValidatedError(
                message,
                detail={
                    "message": message,
                    "conflicting_periods": [],
                    "unresolved_rows": [
                        item.model_dump(mode="json") for item in unresolved
                    ],
                },
            )

        rows = [
            ImportPayrollRowDTO(
                employer=period.employer,
                period_year=period.period_year,
                period_month=period.period_month,
                payment_date=period.payment_date,
                concept_code=row.concept_code,
                amount_clp=row.amount_clp,
                worked_days=period.worked_days,
                declared_net_pay_clp=period.declared_net_pay_clp,
                period_id=period.period_id,
                require_period_contract_validation=True,
            )
            for period in payload.periods
            for row in period.rows
            if row.concept_code is not None
        ]

        if any(period.rows for period in payload.periods) and not rows:
            # Every submitted row across every period lacks a concept_code
            # (commit already raised above, so we can only get here in
            # mode="validate"): nothing to persist yet, but this is not an
            # error.
            result = ImportPayrollResultDTO(
                imported_periods=0, imported_items=0, periods=[]
            )
        else:
            # Either every row is resolved, or every period's rows were
            # empty to begin with -- in which case from_rows([])'s own
            # "must not be empty" guard raises, unchanged from before this
            # feature.
            result = await use_case.from_rows(rows)
            result = await process_use_case.execute(result)

        unresolved_period_indices = {item.period_index for item in unresolved}
        period_index_by_key = {
            (period.employer, period.period_year, period.period_month): index
            for index, period in enumerate(payload.periods)
        }
        periods_read = [
            to_imported_period_read(
                period,
                mode=payload.mode,
                has_unresolved=period_index_by_key.get(
                    (period.employer, period.period_year, period.period_month)
                )
                in unresolved_period_indices,
            )
            for period in result.periods
        ]
        validated = not unresolved and is_import_fully_validated(result.periods)
        if payload.mode == "commit" and not validated:
            message = (
                "Cannot commit: computed contributions/net pay do not match "
                "the declared amounts for one or more periods. Resend with "
                'mode="validate" to inspect the warnings, or fix the '
                "underlying data first."
            )
            raise PayrollImportNotValidatedError(
                message,
                detail=build_reconciliation_conflict_detail(message, result.periods),
            )
        if payload.mode == "validate" and not validated:
            message = (
                "Import validation failed: one or more rows have no resolved "
                "concept_code, and/or computed contributions/net pay do not "
                "match the declared amounts for one or more periods. See "
                "unresolved_rows and conflicting_periods in this error's "
                "detail for specifics."
            )
            detail = build_reconciliation_conflict_detail(message, result.periods)
            detail["unresolved_rows"] = [
                item.model_dump(mode="json") for item in unresolved
            ]
            raise PayrollImportNotValidatedError(message, detail=detail)
    except PayrollError as exc:
        await scope.resolve("validate")
        raise to_http_exception(exc, default_status=400) from exc

    await scope.resolve(payload.mode)

    validated_period_count, unvalidated_period_count = count_validated_periods(
        periods_read
    )
    return ImportPayrollResponse(
        mode=payload.mode,
        validated=validated,
        saved=True if payload.mode == "commit" else None,
        period_count=result.imported_periods,
        item_count=result.imported_items,
        validated_period_count=validated_period_count,
        unvalidated_period_count=unvalidated_period_count,
        periods=periods_read,
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


@router.post("/pdf-preview", response_model=list[PdfImportPreviewResponse])
async def preview_pdf_import(
    files: list[UploadFile] = File(...),
    use_case: PreviewPdfImport = Depends(get_preview_pdf_import_use_case),
) -> list[PdfImportPreviewResponse]:
    """Preview payroll data extracted from one or more PDFs.

    Accepts a batch of PDF payslips in a single request (e.g. several
    distinct liquidaciones for different employees or periods) and returns
    one preview per file, in the same order they were uploaded. Each file is
    extracted fully independently -- one payslip's template match or
    resolved rows never influence another's.

    Read-only: never touches PayrollRepository nor
    ProcessImportedPayrollPeriods, and never persists anything. A PDF that
    matches no known template still contributes a 200-worthy entry with
    unresolved rows instead of failing the whole batch -- see
    PreviewPdfImport / TemplatePdfPayrollExtractor. If any file in the batch
    has no filename or fails extraction outright, the entire request fails
    with 400 and nothing is returned, rather than silently dropping that one
    file from the response.
    """
    try:
        previews = []
        for file in files:
            if not file.filename:
                raise HTTPException(
                    status_code=400, detail="A PDF file name is required."
                )
            previews.append(await use_case.execute(file.filename, await file.read()))
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc

    return [to_pdf_import_preview_response(preview) for preview in previews]


async def _execute_delete_payroll_periods(
    period_ids: list[int],
    scope: TransactionalSessionScope,
    use_case: object,
) -> Response:
    """Execute a delete request and resolve its transaction exactly once."""
    try:
        await use_case.execute(period_ids)  # type: ignore[attr-defined]
    except PayrollError as exc:
        await scope.resolve("validate")
        raise to_http_exception(exc, default_status=400) from exc
    await scope.resolve("commit")
    return Response(status_code=204)


@router.delete("", status_code=204)
async def delete_payroll_periods(
    payload: DeletePayrollPeriodsRequest,
    scope: TransactionalSessionScope = Depends(get_transactional_session),
    use_case=Depends(get_transactional_delete_payroll_periods_use_case),
) -> Response:
    """Delete all requested payroll periods atomically."""
    return await _execute_delete_payroll_periods(payload.period_ids, scope, use_case)


@router.delete("/{period_id}", status_code=204)
async def delete_payroll_period(
    period_id: int = Path(..., gt=0),
    scope: TransactionalSessionScope = Depends(get_transactional_session),
    use_case=Depends(get_transactional_delete_payroll_periods_use_case),
) -> Response:
    """Delete one payroll period atomically."""
    return await _execute_delete_payroll_periods([period_id], scope, use_case)


@router.get("", response_model=list[PayrollPeriodRead])
async def list_payroll_periods(
    previous_months: int | None = Query(
        None,
        ge=0,
        description=(
            "How many previous periods to include. Defaults to 12 when "
            "omitted, padding missing history with inferred placeholders "
            "(today's established behavior). Passing this explicitly (even "
            "as 12) disables that padding -- only periods genuinely in the "
            "database are returned, up to this count."
        ),
    ),
    future_months: int | None = Query(
        None,
        ge=0,
        le=12,
        description=(
            "How many future (projected) periods to include. Defaults to "
            "12, capped at 12."
        ),
    ),
    queries: PayrollQueries = Depends(get_payroll_queries),
) -> list[PayrollPeriodRead]:
    """List payroll periods around the current period.

    Unifies the previously separate GET /payroll/period-range and GET
    /payroll/summary into a single response shape -- see PayrollPeriodRead's
    own docstring for exactly which fields are None on synthetic entries.
    """
    return to_payroll_period_reads(
        await queries.list_period_ranges(
            previous_months=previous_months, future_months=future_months
        )
    )


@router.get("/{period_id}", response_model=PayrollPeriodRead)
async def get_payroll_period(
    period_id: int = Path(..., gt=0),
    queries: PayrollQueries = Depends(get_payroll_queries),
) -> PayrollPeriodRead:
    """Get a single payroll period in the same shape as GET /payroll.

    Works for any period_id regardless of age -- not bounded by GET
    /payroll's own default previous_months/future_months window.
    """
    try:
        context = await queries.get_period_range(period_id)
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=404) from exc
    return to_payroll_period_read(context)
