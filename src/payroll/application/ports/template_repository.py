"""Port definitions for PDF template storage (pf-db PAY_PDF_TEMPLATE*)."""

from typing import Protocol

from payroll.application.dto import PdfTemplateDTO


class TemplateReader(Protocol):
    """Narrow, read-only port for resolving the active PDF templates.

    Deliberately its own Protocol rather than a method tacked onto
    TemplateRepository below -- PreviewPdfImport (the only consumer that
    needs this) only ever needs this one lookup, not the whole
    create/update/deactivate surface, so depending on the narrower port
    keeps it honest about what it actually uses (interface segregation).
    Mirrors the existing EmployerPaymentRuleReader / ReferenceDataRepository
    split in application/ports/repositories.py.
    """

    async def list_active_templates(self) -> list[PdfTemplateDTO]:
        """List every active (is_active=True) template, fields included."""
        ...


class TemplateRepository(Protocol):
    """Persistence port for managing PDF templates (CRUD + logical delete).

    A single concrete adapter (SqlAlchemyTemplateRepository) satisfies both
    this Protocol and TemplateReader above -- one class, two Protocols,
    same pattern SqlAlchemyReferenceDataRepository already uses.
    """

    async def list_templates(
        self, *, include_inactive: bool = False
    ) -> list[PdfTemplateDTO]:
        """List templates, active-only by default."""
        ...

    async def get_template(
        self, template_id: str, *, include_inactive: bool = False
    ) -> PdfTemplateDTO | None:
        """Get one template by its external template_id, or None if not found."""
        ...

    async def create_template(self, template: PdfTemplateDTO) -> PdfTemplateDTO:
        """Create a new template (and its fields). Raises on duplicate template_id."""
        ...

    async def update_template(
        self, template_id: str, template: PdfTemplateDTO
    ) -> PdfTemplateDTO | None:
        """Replace an existing template's fields in place (no version bump).

        Returns None when no template with that template_id exists, letting
        the caller translate that into a 404 -- this port never raises for
        "not found", only for genuine constraint violations (e.g. an unknown
        concept_code).
        """
        ...

    async def deactivate_template(self, template_id: str) -> PdfTemplateDTO | None:
        """Logically delete a template (is_active -> false). Never a row DELETE.

        Returns None when no template with that template_id exists.
        """
        ...
