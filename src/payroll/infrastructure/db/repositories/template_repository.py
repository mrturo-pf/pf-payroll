"""SQLAlchemy repository for PDF payslip templates (PAY_PDF_TEMPLATE*).

Implements both `TemplateReader` (read-only, used by `PreviewPdfImport`) and
`TemplateRepository` (full CRUD, used by the `/payroll/templates` routes) --
one concrete class satisfying two Protocols, mirroring how
`SqlAlchemyReferenceDataRepository` already satisfies both
`EmployerPaymentRuleReader` and the fuller `ReferenceDataRepository`.

None of `PdfTemplateFieldModel.kind`, `PdfTemplateModel.employer_name`, nor
`PdfTemplateFieldModel.concept_code` exist anymore (see pf-db migrations
0010/0011/0012) -- all three used to duplicate data already owned by
`PAY_CONCEPT`/`PAY_EMPLOYER` (the first two with no referential integrity
tying the copies together; the third as a plain inconsistency with every
other FK into `PAY_CONCEPT` in the schema, which all reference its surrogate
`id`). `PdfTemplateModel.employer_id` is now required (migration 0012) -- a
template may only be created for an employer that already has a
`PAY_EMPLOYER` row. This repository is the one place that resolves the
display name and field kinds, fresh, on every read and write.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from payroll.application.dto import (
    ConceptRef,
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
    "Could not save template: duplicate template_id, a field references an "
    "unknown concept_code, or employer_id does not exist."
)


def _to_dto(
    model: PdfTemplateModel,
    concepts_by_id: dict[int, tuple[str, str]],
    employer_names: dict[int, str],
) -> PdfTemplateDTO:
    """Map a PdfTemplateModel (fields eagerly loaded) to a PdfTemplateDTO.

    `concepts_by_id`/`employer_names` are pre-resolved batches (see
    `_dto_lookup_dicts()`) -- this function does no I/O of its own, so it
    stays trivially testable without a session. `concepts_by_id` maps
    `concept_id -> (code, kind)`: one lookup resolves both values a field
    needs, since both live on the same `PAY_CONCEPT` row. `employer_id` is
    required (NOT NULL, see pf-db migration 0012), so `employer_names` is
    expected to always have an entry for it -- a loaded model's employer_id
    is guaranteed to reference a real PAY_EMPLOYER row by the FK itself.
    """
    return PdfTemplateDTO(
        id=model.id,
        template_id=model.template_id,
        employer_id=model.employer_id,
        employer_name=employer_names[model.employer_id],
        employer_match_pattern=model.employer_match_pattern,
        version=model.version,
        is_active=model.is_active,
        fields=[
            PdfTemplateFieldDTO(
                id=field.id,
                pdf_label_pattern=field.pdf_label_pattern,
                concept_code=concepts_by_id[field.concept_id][0],
                kind=concepts_by_id[field.concept_id][1],  # type: ignore[arg-type]
                confidence=float(field.confidence),
            )
            for field in model.fields
        ],
    )


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
        concepts_by_id, employer_names = await self._dto_lookup_dicts(models)
        return [_to_dto(model, concepts_by_id, employer_names) for model in models]

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
        concepts_by_id, employer_names = await self._dto_lookup_dicts([model])
        return _to_dto(model, concepts_by_id, employer_names)

    async def create_template(self, template: PdfTemplateDTO) -> PdfTemplateDTO:
        """Create a new template (and its fields). Raises on duplicate template_id."""
        await self._validate_employer_exists(template.employer_id)
        model = PdfTemplateModel(
            template_id=template.template_id,
            employer_id=template.employer_id,
            employer_match_pattern=template.employer_match_pattern,
            version=template.version,
            is_active=True,
            fields=await self._to_field_models(template.fields),
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
        await self._validate_employer_exists(template.employer_id)
        model.employer_id = template.employer_id
        model.employer_match_pattern = template.employer_match_pattern
        model.version = template.version
        # Reassigning the relationship (cascade="all, delete-orphan") deletes
        # every previous field row and inserts the new ones in one flush --
        # no hand-rolled DELETE statement needed.
        model.fields = await self._to_field_models(template.fields)
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

    async def resolve_concepts(self, codes: set[str]) -> dict[str, ConceptRef]:
        """Resolve each code's real PAY_CONCEPT id + kind, keyed by concept_code."""
        if not codes:
            return {}
        statement = select(
            PayrollConceptModel.code, PayrollConceptModel.id, PayrollConceptModel.kind
        ).where(PayrollConceptModel.code.in_(codes))
        result = await self._session.execute(statement)
        return {
            code: ConceptRef(id=concept_id, kind=kind.value)
            for code, concept_id, kind in result.all()
        }

    async def _to_field_models(
        self, fields: list[PdfTemplateFieldDTO]
    ) -> list[PdfTemplateFieldModel]:
        """Build fresh PdfTemplateFieldModel rows, resolving id from concept_code.

        Re-resolves independently of any earlier resolution the caller (the
        `/payroll/templates` routes) already did -- this repository stays
        correct even if invoked without that pre-validation (e.g. from a
        test, or a future non-HTTP caller), raising the same
        PayrollValidationError a route would instead of leaning solely on
        the FK IntegrityError fallback. `field.kind` is intentionally
        ignored -- PdfTemplateFieldModel has no `kind` column to set (see
        module docstring); `_to_dto()` re-resolves the real value on every
        way out.
        """
        codes = {field.concept_code for field in fields}
        concepts = await self.resolve_concepts(codes)
        unknown = codes - concepts.keys()
        if unknown:
            raise PayrollValidationError(
                f"Unknown concept_code(s): {', '.join(sorted(unknown))}."
            )
        return [
            PdfTemplateFieldModel(
                pdf_label_pattern=field.pdf_label_pattern,
                concept_id=concepts[field.concept_code].id,
                confidence=Decimal(str(field.confidence)),
            )
            for field in fields
        ]

    async def _load_employer_names(self, employer_ids: set[int]) -> dict[int, str]:
        """Resolve PAY_EMPLOYER.name for a set of ids -- the read path's names."""
        if not employer_ids:
            return {}
        statement = select(EmployerModel.id, EmployerModel.name).where(
            EmployerModel.id.in_(employer_ids)
        )
        result = await self._session.execute(statement)
        return {employer_id: name for employer_id, name in result.all()}

    async def _validate_employer_exists(self, employer_id: int) -> None:
        """Reject an unknown employer_id with a clear 400, on the write path.

        Mirrors _to_field_models()'s unknown-concept_code check: rejecting
        explicitly here is earlier and clearer than leaning solely on the FK
        IntegrityError translated by _commit_or_raise(). A template may only
        be created for an employer that already has a PAY_EMPLOYER row (see
        pf-db migration 0012) -- there is no standalone endpoint to create
        one ahead of that.
        """
        if not await self._load_employer_names({employer_id}):
            raise PayrollValidationError(f"Unknown employer_id: {employer_id}.")

    async def _load_concepts_by_id(
        self, concept_ids: set[int]
    ) -> dict[int, tuple[str, str]]:
        """Resolve (code, kind) for a set of PAY_CONCEPT ids, for the read path.

        The mirror image of resolve_concepts() (which looks up by code, for
        the write path) -- purely internal to this repository's own
        _to_dto() mapping, so it returns a plain tuple rather than the
        public ConceptRef (which is shaped for the by-code write lookup and
        would be redundant here: the dict key already *is* the id).
        """
        if not concept_ids:
            return {}
        statement = select(
            PayrollConceptModel.id, PayrollConceptModel.code, PayrollConceptModel.kind
        ).where(PayrollConceptModel.id.in_(concept_ids))
        result = await self._session.execute(statement)
        return {
            concept_id: (code, kind.value) for concept_id, code, kind in result.all()
        }

    async def _dto_lookup_dicts(
        self, models: list[PdfTemplateModel]
    ) -> tuple[dict[int, tuple[str, str]], dict[int, str]]:
        """Batch-resolve everything _to_dto() needs for a set of models.

        Shared by list_templates()/get_template() (get_template just passes a
        1-element list) -- one pair of queries regardless of how many
        templates/fields are involved, not N+1, and the resolution logic
        lives in exactly one place.
        """
        concept_ids = {field.concept_id for model in models for field in model.fields}
        employer_ids = {model.employer_id for model in models}
        concepts_by_id = await self._load_concepts_by_id(concept_ids)
        employer_names = await self._load_employer_names(employer_ids)
        return concepts_by_id, employer_names

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

        A duplicate template_id (UNIQUE) is the realistic case left by the
        time this runs -- an unknown concept_code and an unknown employer_id
        are both already rejected explicitly, earlier, by
        _to_field_models()/_validate_employer_exists(). Kept as a safety net
        regardless: a Pydantic request validator cannot catch any of these
        without a real database round-trip.
        """
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise PayrollValidationError(
                _INTEGRITY_ERROR_MESSAGE, detail=_INTEGRITY_ERROR_MESSAGE
            ) from exc
