"""SQLAlchemy repository for PDF payslip templates (PAY_PDF_TEMPLATE*).

Implements both `TemplateReader` (read-only, used by `PreviewPdfImport`) and
`TemplateRepository` (full CRUD, used by the `/payroll/templates` routes) --
one concrete class satisfying two Protocols, mirroring how
`SqlAlchemyReferenceDataRepository` already satisfies both
`EmployerPaymentRuleReader` and the fuller `ReferenceDataRepository`.

Neither `PdfTemplateFieldModel.kind` nor an unconditional
`PdfTemplateModel.employer_name` exist anymore (see pf-db migration 0010) --
both used to duplicate data already owned by `PAY_CONCEPT`/`PAY_EMPLOYER` with
no referential integrity tying the copies together. This repository is the
one place that resolves both, fresh, on every read.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from payroll.application.dto import (
    PayrollConceptKind,
    PdfTemplateDTO,
    PdfTemplateFieldDTO,
)
from payroll.application.errors import PayrollValidationError
from payroll.infrastructure.db.models.payroll import EmployerModel
from payroll.infrastructure.db.models.pdf_template import (
    PdfTemplateFieldModel,
    PdfTemplateModel,
)
from payroll.infrastructure.db.models.reference_data import PayrollConceptModel

_INTEGRITY_ERROR_MESSAGE = (
    "Could not save template: duplicate template_id, or a field references an "
    "unknown concept_code."
)


def _to_dto(
    model: PdfTemplateModel,
    concept_kinds: dict[str, PayrollConceptKind],
    employer_names: dict[int, str],
) -> PdfTemplateDTO:
    """Map a PdfTemplateModel (fields eagerly loaded) to a PdfTemplateDTO.

    `concept_kinds`/`employer_names` are pre-resolved batches (see
    `_dto_lookup_dicts()`) -- this function does no I/O of its own, so it
    stays trivially testable without a session.
    """
    employer_name = model.employer_name
    if employer_name is None and model.employer_id is not None:
        employer_name = employer_names.get(model.employer_id)
    return PdfTemplateDTO(
        id=model.id,
        template_id=model.template_id,
        employer_id=model.employer_id,
        employer_name=employer_name,
        employer_match_pattern=model.employer_match_pattern,
        version=model.version,
        is_active=model.is_active,
        fields=[
            PdfTemplateFieldDTO(
                id=field.id,
                pdf_label_pattern=field.pdf_label_pattern,
                concept_code=field.concept_code,
                kind=concept_kinds[field.concept_code],
                confidence=float(field.confidence),
            )
            for field in model.fields
        ],
    )


def _to_field_models(fields: list[PdfTemplateFieldDTO]) -> list[PdfTemplateFieldModel]:
    """Build fresh PdfTemplateFieldModel rows from a list of field DTOs.

    `field.kind` is intentionally ignored here -- PdfTemplateFieldModel has no
    `kind` column to set (see module docstring). Whatever kind the caller put
    on the DTO (routes always resolve it from PAY_CONCEPT before constructing
    one, see `pdf_templates.py`) is informational only on the way in; `_to_dto`
    re-resolves the real value on every way out.
    """
    return [
        PdfTemplateFieldModel(
            pdf_label_pattern=field.pdf_label_pattern,
            concept_code=field.concept_code,
            confidence=Decimal(str(field.confidence)),
        )
        for field in fields
    ]


class SqlAlchemyTemplateRepository:
    """Provide SQLAlchemy-backed PDF template storage."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the instance."""
        self._session = session

    async def list_active_templates(self) -> list[PdfTemplateDTO]:
        """List every active template, fields included.

        Thin wrapper over list_templates() -- one query implementation, no
        duplicated matching/filter logic between the two Protocols this
        class satisfies.
        """
        return await self.list_templates(include_inactive=False)

    async def list_templates(
        self, *, include_inactive: bool = False
    ) -> list[PdfTemplateDTO]:
        """List templates, active-only by default."""
        statement = (
            select(PdfTemplateModel)
            .options(selectinload(PdfTemplateModel.fields))
            .order_by(PdfTemplateModel.template_id)
        )
        if not include_inactive:
            statement = statement.where(PdfTemplateModel.is_active.is_(True))
        result = await self._session.execute(statement)
        models = list(result.scalars().all())
        concept_kinds, employer_names = await self._dto_lookup_dicts(models)
        return [_to_dto(model, concept_kinds, employer_names) for model in models]

    async def get_template(
        self, template_id: str, *, include_inactive: bool = False
    ) -> PdfTemplateDTO | None:
        """Get one template by its external template_id, or None if not found."""
        statement = (
            select(PdfTemplateModel)
            .options(selectinload(PdfTemplateModel.fields))
            .where(PdfTemplateModel.template_id == template_id)
        )
        if not include_inactive:
            statement = statement.where(PdfTemplateModel.is_active.is_(True))
        result = await self._session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        concept_kinds, employer_names = await self._dto_lookup_dicts([model])
        return _to_dto(model, concept_kinds, employer_names)

    async def create_template(self, template: PdfTemplateDTO) -> PdfTemplateDTO:
        """Create a new template (and its fields). Raises on duplicate template_id."""
        model = PdfTemplateModel(
            template_id=template.template_id,
            employer_id=template.employer_id,
            employer_name=template.employer_name,
            employer_match_pattern=template.employer_match_pattern,
            version=template.version,
            is_active=True,
            fields=_to_field_models(template.fields),
        )
        self._session.add(model)
        await self._commit_or_raise()
        created = await self.get_template(template.template_id, include_inactive=True)
        if created is None:  # pragma: no cover -- row was just committed successfully
            raise PayrollValidationError(
                "Template was created but could not be re-read."
            )
        return created

    async def update_template(
        self, template_id: str, template: PdfTemplateDTO
    ) -> PdfTemplateDTO | None:
        """Replace an existing template's fields in place (no version bump)."""
        model = await self._get_model(template_id)
        if model is None:
            return None
        model.employer_id = template.employer_id
        model.employer_name = template.employer_name
        model.employer_match_pattern = template.employer_match_pattern
        model.version = template.version
        # Reassigning the relationship (cascade="all, delete-orphan") deletes
        # every previous field row and inserts the new ones in one flush --
        # no hand-rolled DELETE statement needed.
        model.fields = _to_field_models(template.fields)
        await self._commit_or_raise()
        return await self.get_template(template_id, include_inactive=True)

    async def deactivate_template(self, template_id: str) -> PdfTemplateDTO | None:
        """Logically delete a template (is_active -> false). Idempotent."""
        model = await self._get_model(template_id)
        if model is None:
            return None
        model.is_active = False
        await self._session.commit()
        return await self.get_template(template_id, include_inactive=True)

    async def resolve_concept_kinds(
        self, codes: set[str]
    ) -> dict[str, PayrollConceptKind]:
        """Resolve each code's real PAY_CONCEPT.kind, keyed by concept_code."""
        if not codes:
            return {}
        statement = select(PayrollConceptModel.code, PayrollConceptModel.kind).where(
            PayrollConceptModel.code.in_(codes)
        )
        result = await self._session.execute(statement)
        return {code: kind.value for code, kind in result.all()}

    async def _load_employer_names(self, employer_ids: set[int]) -> dict[int, str]:
        """Resolve PAY_EMPLOYER.name for a set of ids, to fill a NULL employer_name."""
        if not employer_ids:
            return {}
        statement = select(EmployerModel.id, EmployerModel.name).where(
            EmployerModel.id.in_(employer_ids)
        )
        result = await self._session.execute(statement)
        return {employer_id: name for employer_id, name in result.all()}

    async def _dto_lookup_dicts(
        self, models: list[PdfTemplateModel]
    ) -> tuple[dict[str, PayrollConceptKind], dict[int, str]]:
        """Batch-resolve everything _to_dto() needs for a set of models.

        Shared by list_templates()/get_template() (get_template just passes a
        1-element list) -- one pair of queries regardless of how many
        templates/fields are involved, not N+1, and the resolution logic
        lives in exactly one place.
        """
        codes = {field.concept_code for model in models for field in model.fields}
        employer_ids = {
            model.employer_id
            for model in models
            if model.employer_name is None and model.employer_id is not None
        }
        concept_kinds = await self.resolve_concept_kinds(codes)
        employer_names = await self._load_employer_names(employer_ids)
        return concept_kinds, employer_names

    async def _get_model(self, template_id: str) -> PdfTemplateModel | None:
        """Fetch the raw model (fields included) by template_id, active or not.

        No `include_inactive` parameter -- unlike `get_template()` above, both
        callers (`update_template`, `deactivate_template`) always need to find
        the row regardless of its current `is_active` value (you must be able
        to PUT/DELETE an already-deactivated template), so there was never a
        real caller for an active-only variant of this private helper.
        """
        statement = (
            select(PdfTemplateModel)
            .options(selectinload(PdfTemplateModel.fields))
            .where(PdfTemplateModel.template_id == template_id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def _commit_or_raise(self) -> None:
        """Commit, translating an IntegrityError into a 400 PayrollValidationError.

        Covers both failure modes a Pydantic request validator cannot catch
        on its own: a duplicate template_id (UNIQUE), and a field whose
        concept_code does not exist in PAY_CONCEPT (FK) -- neither is
        knowable without a real database round-trip.
        """
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise PayrollValidationError(
                _INTEGRITY_ERROR_MESSAGE, detail=_INTEGRITY_ERROR_MESSAGE
            ) from exc
