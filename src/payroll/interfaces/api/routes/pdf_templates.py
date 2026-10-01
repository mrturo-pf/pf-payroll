"""PDF template management routes: create/list/get/modify/logical-delete.

The first genuinely CRUD-shaped resource this API manages -- standard REST
verbs (`POST`/`GET`/`PUT`/`DELETE`) are used deliberately, unlike the rest of
this API's `POST`-only, action-shaped mutations (`/{period_id}/review`,
`/{period_id}/compute-tax`, ...). See
`docs/proposals/pdf-template-management-design-recommendation.md` for the
full rationale. Kept in its own router module for the same cohesion reason
`payroll_export.py` is its own file, not appended to the already-large
`payroll.py`.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from payroll.application.dto import PdfTemplateDTO, PdfTemplateFieldDTO
from payroll.application.errors import (
    PayrollError,
    PayrollValidationError,
    PdfTemplateNotFoundError,
)
from payroll.application.ports.template_repository import TemplateRepository
from payroll.interfaces.api.dependencies import get_template_repository
from payroll.interfaces.api.errors import to_http_exception

router = APIRouter(prefix="/payroll", tags=["payroll"])


def _compiles_as_regex(value: str) -> str:
    """Validate a pattern compiles, raising a field-level 422 if it doesn't.

    Closes the gap the design brief called out explicitly: before this
    endpoint existed, an invalid pattern in a hand-edited JSON file would
    only ever surface as a crash the next time an unrelated PDF was
    previewed. Now it is rejected here, before any database write.
    """
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"Invalid regex: {exc}") from exc
    return value


class TemplateFieldRequest(BaseModel):
    """Represent one label-to-concept mapping rule in a create/update request.

    No `kind` field -- it would duplicate PAY_CONCEPT.kind with no way for
    this schema to guarantee the two stay in sync. The server always resolves
    kind from `concept_code` instead (see `_resolve_field_dtos()` below); a
    `concept_code` with no matching PAY_CONCEPT row is a 400, not a bad guess.
    """

    pdf_label_pattern: str
    concept_code: str
    confidence: float = 0.9

    @field_validator("pdf_label_pattern")
    @classmethod
    def _validate_pdf_label_pattern(cls, value: str) -> str:
        """Delegate to _compiles_as_regex (cls unused -- required by @classmethod)."""
        del cls
        return _compiles_as_regex(value)


class TemplateWriteRequest(BaseModel):
    """Share the fields common to create and update requests.

    `employer_name` is optional: omit it when `employer_id` is given and the
    canonical PAY_EMPLOYER.name should be used as the display name (resolved
    fresh on every read, never copied into the row -- see pf-db migration
    0010). At least one of the two must be provided, so a template can always
    resolve *some* display name.
    """

    employer_name: str | None = None
    employer_match_pattern: str
    version: int = 1
    employer_id: int | None = None
    fields: list[TemplateFieldRequest] = Field(min_length=1)

    @field_validator("employer_match_pattern")
    @classmethod
    def _validate_employer_match_pattern(cls, value: str) -> str:
        """Delegate to _compiles_as_regex (cls unused -- required by @classmethod)."""
        del cls
        return _compiles_as_regex(value)

    @model_validator(mode="after")
    def _require_employer_name_or_id(self) -> TemplateWriteRequest:
        """Mirror pf-db's chk_pay_pdf_template_employer_ref CHECK constraint."""
        if self.employer_id is None and self.employer_name is None:
            raise ValueError("Provide employer_name, employer_id, or both.")
        return self


class TemplateCreateRequest(TemplateWriteRequest):
    """Represent a POST /payroll/templates request body."""

    template_id: str


class TemplateUpdateRequest(TemplateWriteRequest):
    """Represent a PUT /payroll/templates/{template_id} request body."""


class TemplateFieldRead(BaseModel):
    """Represent one template field in an API response."""

    id: int
    pdf_label_pattern: str
    concept_code: str
    kind: str
    confidence: float


class TemplateRead(BaseModel):
    """Represent a full template in an API response."""

    id: int
    template_id: str
    employer_id: int | None
    employer_name: str
    employer_match_pattern: str
    version: int
    is_active: bool
    fields: list[TemplateFieldRead]


def _to_read_model(dto: PdfTemplateDTO) -> TemplateRead:
    """Map a PdfTemplateDTO to its API response model.

    `dto.id`/`field.id` are typed `int | None` on PdfTemplateDTO (`None` only
    for a not-yet-persisted create request, see that dataclass's docstring)
    -- always set here, since every DTO reaching this function came back
    from a repository read. `dto.employer_name` is `str | None` for the same
    reason -- always resolved (never None) by the time a repository read
    reaches here.
    """
    return TemplateRead(
        id=dto.id,  # type: ignore[arg-type]
        template_id=dto.template_id,
        employer_id=dto.employer_id,
        employer_name=dto.employer_name,  # type: ignore[arg-type]
        employer_match_pattern=dto.employer_match_pattern,
        version=dto.version,
        is_active=dto.is_active,
        fields=[
            TemplateFieldRead(
                id=field.id,  # type: ignore[arg-type]
                pdf_label_pattern=field.pdf_label_pattern,
                concept_code=field.concept_code,
                kind=field.kind,
                confidence=field.confidence,
            )
            for field in dto.fields
        ],
    )


async def _resolve_field_dtos(
    fields: list[TemplateFieldRequest], repository: TemplateRepository
) -> list[PdfTemplateFieldDTO]:
    """Resolve each field's kind from PAY_CONCEPT, never trusting a client value.

    Raises PayrollValidationError (-> 400) for any concept_code with no
    matching PAY_CONCEPT row, before ever attempting to write a field --
    earlier and clearer than letting the FK constraint fail during commit.
    """
    codes = {field.concept_code for field in fields}
    concept_kinds = await repository.resolve_concept_kinds(codes)
    unknown = codes - concept_kinds.keys()
    if unknown:
        raise PayrollValidationError(
            f"Unknown concept_code(s): {', '.join(sorted(unknown))}."
        )
    return [
        PdfTemplateFieldDTO(
            id=None,
            pdf_label_pattern=field.pdf_label_pattern,
            concept_code=field.concept_code,
            kind=concept_kinds[field.concept_code],
            confidence=field.confidence,
        )
        for field in fields
    ]


@router.post("/templates", response_model=TemplateRead, status_code=201)
async def create_template(
    request: TemplateCreateRequest,
    repository: TemplateRepository = Depends(get_template_repository),
) -> TemplateRead:
    """Create a new PDF template. 400 on duplicate template_id or bad concept_code."""
    try:
        fields = await _resolve_field_dtos(request.fields, repository)
        dto = PdfTemplateDTO(
            id=None,
            template_id=request.template_id,
            employer_id=request.employer_id,
            employer_name=request.employer_name,
            employer_match_pattern=request.employer_match_pattern,
            version=request.version,
            is_active=True,
            fields=fields,
        )
        created = await repository.create_template(dto)
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc
    return _to_read_model(created)


@router.get("/templates", response_model=list[TemplateRead])
async def list_templates(
    include_inactive: bool = Query(default=False),
    repository: TemplateRepository = Depends(get_template_repository),
) -> list[TemplateRead]:
    """List templates, active-only by default (?include_inactive=true shows all)."""
    templates = await repository.list_templates(include_inactive=include_inactive)
    return [_to_read_model(template) for template in templates]


@router.get("/templates/{template_id}", response_model=TemplateRead)
async def get_template(
    template_id: str,
    repository: TemplateRepository = Depends(get_template_repository),
) -> TemplateRead:
    """Get one template by its external template_id, active or not."""
    template = await repository.get_template(template_id, include_inactive=True)
    if template is None:
        raise to_http_exception(
            PdfTemplateNotFoundError(
                f"No template found with template_id={template_id!r}."
            )
        )
    return _to_read_model(template)


@router.put("/templates/{template_id}", response_model=TemplateRead)
async def update_template(
    template_id: str,
    request: TemplateUpdateRequest,
    repository: TemplateRepository = Depends(get_template_repository),
) -> TemplateRead:
    """Replace an existing template's fields in place (mutate, no version bump)."""
    try:
        fields = await _resolve_field_dtos(request.fields, repository)
        dto = PdfTemplateDTO(
            id=None,
            template_id=template_id,
            employer_id=request.employer_id,
            employer_name=request.employer_name,
            employer_match_pattern=request.employer_match_pattern,
            version=request.version,
            is_active=True,
            fields=fields,
        )
        updated = await repository.update_template(template_id, dto)
    except PayrollError as exc:
        raise to_http_exception(exc, default_status=400) from exc
    if updated is None:
        raise to_http_exception(
            PdfTemplateNotFoundError(
                f"No template found with template_id={template_id!r}."
            )
        )
    return _to_read_model(updated)


@router.delete("/templates/{template_id}", response_model=TemplateRead)
async def deactivate_template(
    template_id: str,
    repository: TemplateRepository = Depends(get_template_repository),
) -> TemplateRead:
    """Logically delete a template (is_active -> false). Never a row DELETE."""
    deactivated = await repository.deactivate_template(template_id)
    if deactivated is None:
        raise to_http_exception(
            PdfTemplateNotFoundError(
                f"No template found with template_id={template_id!r}."
            )
        )
    return _to_read_model(deactivated)
