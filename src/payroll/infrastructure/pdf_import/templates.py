"""Payroll PDF templates: employer-specific label -> concept_code mapping.

Templates are plain JSON files versioned in git under `templates/<employer
slug>/v<N>.json` -- never in the database (see docs/proposals -- this is
config, curated by a human per employer/format, not application data).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from payroll.application.dto import PayrollConceptKind

MIN_TEMPLATE_MATCH_SCORE = 3

_DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "templates"


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


def _load_template_file(path: Path) -> Template:
    """Load and compile a single template JSON file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Template(
        template_id=payload["template_id"],
        employer_name=payload["employer_name"],
        version=payload["version"],
        employer_match=re.compile(payload["employer_match"]["name_pattern"]),
        fields=tuple(
            TemplateField(
                pattern=re.compile(field["pdf_label_pattern"]),
                concept_code=field["concept_code"],
                kind=field["kind"],
                confidence=field.get("confidence", 0.9),
            )
            for field in payload["fields"]
        ),
    )


def load_templates(templates_dir: Path | None = None) -> list[Template]:
    """Load every template JSON file found (recursively) under a directory."""
    directory = templates_dir or _DEFAULT_TEMPLATES_DIR
    if not directory.is_dir():
        return []
    return [_load_template_file(path) for path in sorted(directory.rglob("*.json"))]


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
