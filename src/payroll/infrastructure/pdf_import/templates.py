"""Payroll PDF templates: employer-specific label -> concept_code mapping.

Templates are compiled from `PdfTemplateDTO` instances read out of pf-db's
`PAY_PDF_TEMPLATE` / `PAY_PDF_TEMPLATE_FIELD` tables (via the
`TemplateReader` port, see `application/ports/template_repository.py`) --
no longer from git-tracked JSON files. Management (create/modify/logical
delete) happens through `POST`/`PUT`/`DELETE /payroll/templates*` (see
`interfaces/api/routes/pdf_templates.py`), not by hand-editing a file in
this repo. See
`docs/proposals/pdf-template-management-design-recommendation.md` for why
this moved, and `-design-plan.md` for the migration itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from payroll.application.dto import PayrollConceptKind, PdfTemplateDTO

MIN_TEMPLATE_MATCH_SCORE = 3


@dataclass(frozen=True, slots=True)
class TemplateField:
    """Represent one label-to-concept mapping rule within a template."""

    pattern: re.Pattern[str]
    concept_code: str
    kind: PayrollConceptKind
    confidence: float


@dataclass(frozen=True, slots=True)
class Template:
    """Represent a single versioned payroll PDF template."""

    template_id: str
    employer_name: str
    version: int
    employer_match: re.Pattern[str]
    fields: tuple[TemplateField, ...]


def compile_templates(dtos: list[PdfTemplateDTO]) -> list[Template]:
    """Compile a list of PdfTemplateDTO into matchable Template objects.

    The direct replacement for the former `load_templates()` +
    `_load_template_file()` pair: same compilation responsibility (raw
    pattern strings -> `re.Pattern`), different input source (DTOs from a
    DB read via `TemplateReader`, not JSON files from disk). Regex validity
    is already guaranteed by the time a DTO reaches here -- every pattern
    was compile-checked by a Pydantic validator at write time (see
    `interfaces/api/routes/pdf_templates.py`), so `re.compile()` below is
    never expected to raise for data that went through that endpoint.
    """
    return [
        Template(
            template_id=dto.template_id,
            employer_name=dto.employer_name,
            version=dto.version,
            employer_match=re.compile(dto.employer_match_pattern),
            fields=tuple(
                TemplateField(
                    pattern=re.compile(field.pdf_label_pattern),
                    concept_code=field.concept_code,
                    kind=field.kind,
                    confidence=field.confidence,
                )
                for field in dto.fields
            ),
        )
        for dto in dtos
    ]


def _template_score(template: Template, labels: list[str]) -> int:
    """Count distinct template fields that match at least one detail label."""
    return sum(
        1
        for field in template.fields
        if any(field.pattern.search(label) for label in labels)
    )


def select_template(
    templates: list[Template], raw_text: str, labels: list[str]
) -> Template | None:
    """Pick the best-matching template for a document, or None if too weak.

    Candidates are first filtered by `employer_match` against the full text,
    then ranked by how many of their fields actually match a detail row's
    label. Falls back to None (never guesses) when no employer matches, or
    when the best score does not clear MIN_TEMPLATE_MATCH_SCORE.
    """
    candidates = [t for t in templates if t.employer_match.search(raw_text)]
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda t: (_template_score(t, labels), t.version),
    )
    if _template_score(best, labels) < MIN_TEMPLATE_MATCH_SCORE:
        return None
    return best


def match_field(template: Template, label: str) -> TemplateField | None:
    """Return the first template field whose pattern matches the label."""
    for field in template.fields:
        if field.pattern.search(label):
            return field
    return None
