"""Unit tests for SqlAlchemyTemplateRepository."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from payroll.application.dto import ConceptRef, PdfTemplateDTO, PdfTemplateFieldDTO
from payroll.application.errors import PayrollValidationError
from payroll.infrastructure.db.models.pdf_template import (
    PdfTemplateFieldModel,
    PdfTemplateModel,
)
from payroll.infrastructure.db.models.reference_data import (
    PayrollConceptKind as ModelPayrollConceptKind,
)
from payroll.infrastructure.db.repositories.template_repository import (
    SqlAlchemyTemplateRepository,
    _to_dto,
)

_SALARY_BASE_CONCEPT_ID = 1
# Keyed by concept_id -- the shape _to_dto()'s concepts_by_id parameter expects.
_CONCEPTS_BY_ID = {_SALARY_BASE_CONCEPT_ID: ("SALARY_BASE", "income")}


def _resolve_concepts_row() -> tuple[str, int, ModelPayrollConceptKind]:
    """Build one PAY_CONCEPT row shaped for resolve_concepts() (code, id, kind)."""
    return ("SALARY_BASE", _SALARY_BASE_CONCEPT_ID, ModelPayrollConceptKind.INCOME)


def _concepts_by_id_row() -> tuple[int, str, ModelPayrollConceptKind]:
    """Build one PAY_CONCEPT row shaped for _load_concepts_by_id() (id, code, kind)."""
    return (_SALARY_BASE_CONCEPT_ID, "SALARY_BASE", ModelPayrollConceptKind.INCOME)


def _build_model(*, is_active: bool = True) -> PdfTemplateModel:
    """Build a PdfTemplateModel with one field, fields pre-populated (no lazy load)."""
    model = PdfTemplateModel(
        id=1,
        template_id="acme-v1",
        employer_id=None,
        employer_name="ACME",
        employer_match_pattern="(?i)acme",
        version=1,
        is_active=is_active,
    )
    model.fields = [
        PdfTemplateFieldModel(
            id=1,
            template_id=1,
            pdf_label_pattern="(?i)^SUELDO$",
            concept_id=_SALARY_BASE_CONCEPT_ID,
            confidence=0.9,
        )
    ]
    return model


def _build_model_needing_employer_lookup() -> PdfTemplateModel:
    """Build a model whose employer_name is NULL -- must resolve via employer_id."""
    model = PdfTemplateModel(
        id=2,
        template_id="acme-v2",
        employer_id=5,
        employer_name=None,
        employer_match_pattern="(?i)acme",
        version=1,
        is_active=True,
    )
    model.fields = [
        PdfTemplateFieldModel(
            id=2,
            template_id=2,
            pdf_label_pattern="(?i)^SUELDO$",
            concept_id=_SALARY_BASE_CONCEPT_ID,
            confidence=0.9,
        )
    ]
    return model


def _dto(*, is_active: bool = True) -> PdfTemplateDTO:
    """Build a PdfTemplateDTO matching _build_model()'s shape.

    `concept_code` (not `concept_id`) remains the business-facing identifier
    at the DTO layer -- only the storage column changed (see pf-db migration
    0011), so this DTO shape is unaffected by it.
    """
    return PdfTemplateDTO(
        id=1,
        template_id="acme-v1",
        employer_id=None,
        employer_name="ACME",
        employer_match_pattern="(?i)acme",
        version=1,
        is_active=is_active,
        fields=[
            PdfTemplateFieldDTO(
                id=1,
                pdf_label_pattern="(?i)^SUELDO$",
                concept_code="SALARY_BASE",
                kind="income",
                confidence=0.9,
            )
        ],
    )


def _mock_result(**attrs: object) -> MagicMock:
    """Build one MagicMock Result, pre-wired for whichever attributes a test needs.

    A single instance can safely serve multiple execute() calls that each
    read a *different* attribute (e.g. `.scalars().all()` vs. `.all()` vs.
    `.scalar_one_or_none()`) -- they never collide. Calls that need the same
    attribute populated with different values in sequence (e.g. two `.all()`
    lookups with different row shapes) must use `side_effect` with distinct
    `_mock_result()` instances instead.
    """
    result = MagicMock()
    for name, value in attrs.items():
        getattr(result, name).return_value = value
    return result


def test_to_dto_maps_model_and_fields() -> None:
    """Test _to_dto maps a model (fields included) to the matching DTO."""
    dto = _to_dto(_build_model(), _CONCEPTS_BY_ID, {})
    assert dto == _dto()


def test_to_dto_resolves_employer_name_from_dict_when_column_is_null() -> None:
    """When employer_name is NULL, _to_dto resolves it via the employer_names dict."""
    dto = _to_dto(
        _build_model_needing_employer_lookup(), _CONCEPTS_BY_ID, {5: "ACME S.A."}
    )
    assert dto.employer_name == "ACME S.A."


def test_to_dto_leaves_employer_name_none_when_unresolvable() -> None:
    """Defensive: a missing lookup-dict entry leaves employer_name None."""
    dto = _to_dto(_build_model_needing_employer_lookup(), _CONCEPTS_BY_ID, {})
    assert dto.employer_name is None


@pytest.mark.asyncio
async def test_list_templates_returns_mapped_dtos() -> None:
    """Test list_templates maps every returned model."""
    mock_session = AsyncMock()
    mock_result = _mock_result(all=[_concepts_by_id_row()])
    mock_result.scalars().all.return_value = [_build_model()]
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyTemplateRepository(mock_session)
    templates = await repo.list_templates()

    assert templates == [_dto()]


@pytest.mark.asyncio
async def test_list_active_templates_delegates_to_list_templates() -> None:
    """Test list_active_templates is a thin wrapper, not a second query shape."""
    mock_session = AsyncMock()
    mock_result = _mock_result(all=[_concepts_by_id_row()])
    mock_result.scalars().all.return_value = [_build_model()]
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyTemplateRepository(mock_session)
    templates = await repo.list_active_templates()

    assert templates == [_dto()]


@pytest.mark.asyncio
async def test_get_template_found() -> None:
    """Test get_template returns the mapped DTO when found."""
    mock_session = AsyncMock()
    mock_result = _mock_result(
        scalar_one_or_none=_build_model(),
        all=[_concepts_by_id_row()],
    )
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyTemplateRepository(mock_session)
    template = await repo.get_template("acme-v1")

    assert template == _dto()


@pytest.mark.asyncio
async def test_get_template_resolves_employer_name_when_null() -> None:
    """When employer_name is NULL, the repository joins PAY_EMPLOYER via employer_id."""
    mock_session = AsyncMock()
    needing_lookup = _build_model_needing_employer_lookup()
    main_result = _mock_result(scalar_one_or_none=needing_lookup)
    concept_result = _mock_result(all=[_concepts_by_id_row()])
    employer_result = _mock_result(all=[(5, "ACME S.A.")])
    mock_session.execute.side_effect = [main_result, concept_result, employer_result]

    repo = SqlAlchemyTemplateRepository(mock_session)
    template = await repo.get_template("acme-v2")

    assert template is not None
    assert template.employer_name == "ACME S.A."


@pytest.mark.asyncio
async def test_get_template_not_found_returns_none() -> None:
    """Test get_template returns None, never raises, when nothing matches."""
    mock_session = AsyncMock()
    mock_result = _mock_result(scalar_one_or_none=None)
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyTemplateRepository(mock_session)
    template = await repo.get_template("does-not-exist")

    assert template is None


@pytest.mark.asyncio
async def test_resolve_concepts_maps_code_to_ref() -> None:
    """Test resolve_concepts unwraps the model-layer enum into a plain-str kind."""
    mock_session = AsyncMock()
    mock_session.execute.return_value = _mock_result(all=[_resolve_concepts_row()])

    repo = SqlAlchemyTemplateRepository(mock_session)
    concepts = await repo.resolve_concepts({"SALARY_BASE"})

    assert concepts == {
        "SALARY_BASE": ConceptRef(id=_SALARY_BASE_CONCEPT_ID, kind="income")
    }


@pytest.mark.asyncio
async def test_resolve_concepts_empty_codes_short_circuits() -> None:
    """No codes means no query at all -- avoids a pointless round trip."""
    mock_session = AsyncMock()
    repo = SqlAlchemyTemplateRepository(mock_session)

    concepts = await repo.resolve_concepts(set())

    assert concepts == {}
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_load_concepts_by_id_empty_ids_short_circuits() -> None:
    """No ids means no query at all -- mirrors resolve_concepts()'s short-circuit."""
    mock_session = AsyncMock()
    repo = SqlAlchemyTemplateRepository(mock_session)

    concepts = await repo._load_concepts_by_id(set())  # noqa: SLF001

    assert concepts == {}
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_to_field_models_rejects_unknown_concept_code() -> None:
    """_to_field_models raises, not KeyErrors, on a code resolve_concepts misses.

    Defense in depth: this repository stays correct even if invoked without
    the route layer's own pre-validation (e.g. directly from a test, or a
    future non-HTTP caller).
    """
    mock_session = AsyncMock()
    mock_session.execute.return_value = _mock_result(all=[])

    repo = SqlAlchemyTemplateRepository(mock_session)
    fields = [
        PdfTemplateFieldDTO(
            id=None,
            pdf_label_pattern="(?i)^SUELDO$",
            concept_code="DOES_NOT_EXIST",
            kind="income",
            confidence=0.9,
        )
    ]

    with pytest.raises(PayrollValidationError, match="DOES_NOT_EXIST"):
        await repo._to_field_models(fields)  # noqa: SLF001


@pytest.mark.asyncio
async def test_create_template_commits_and_rereads() -> None:
    """Test create_template resolves concept_id, adds, commits, then re-reads it."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute.side_effect = [
        _mock_result(all=[_resolve_concepts_row()]),  # _to_field_models
        _mock_result(scalar_one_or_none=_build_model()),  # get_template main select
        _mock_result(
            all=[_concepts_by_id_row()]
        ),  # get_template's _load_concepts_by_id
    ]

    repo = SqlAlchemyTemplateRepository(mock_session)
    created = await repo.create_template(_dto())

    mock_session.add.assert_called_once()
    mock_session.commit.assert_awaited_once()
    assert created == _dto()


@pytest.mark.asyncio
async def test_create_template_translates_integrity_error() -> None:
    """A duplicate template_id or unknown concept_code becomes a 400, not a 500.

    Covers both failure modes a Pydantic request validator cannot catch on
    its own (see template_repository.py's _commit_or_raise docstring).
    """
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute.return_value = _mock_result(all=[_resolve_concepts_row()])
    mock_session.commit.side_effect = IntegrityError("INSERT", {}, Exception("dup"))

    repo = SqlAlchemyTemplateRepository(mock_session)

    with pytest.raises(PayrollValidationError):
        await repo.create_template(_dto())

    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_template_not_found_returns_none() -> None:
    """Test update_template returns None (never raises) for an unknown template_id."""
    mock_session = AsyncMock()
    mock_session.execute.return_value = _mock_result(scalar_one_or_none=None)

    repo = SqlAlchemyTemplateRepository(mock_session)
    updated = await repo.update_template("does-not-exist", _dto())

    assert updated is None
    mock_session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_update_template_replaces_fields_in_place() -> None:
    """Test update_template mutates the existing model (no version bump)."""
    existing = _build_model()
    mock_session = AsyncMock()
    mock_session.execute.side_effect = [
        _mock_result(scalar_one_or_none=existing),  # _get_model
        _mock_result(all=[_resolve_concepts_row()]),  # _to_field_models
        _mock_result(scalar_one_or_none=existing),  # get_template main select
        _mock_result(
            all=[_concepts_by_id_row()]
        ),  # get_template's _load_concepts_by_id
    ]

    new_fields = [
        PdfTemplateFieldDTO(
            id=None,
            pdf_label_pattern="(?i)^SUELDO\\s*BASE$",
            concept_code="SALARY_BASE",
            kind="income",
            confidence=0.9,
        )
    ]
    updated_dto = PdfTemplateDTO(
        id=None,
        template_id="acme-v1",
        employer_id=None,
        employer_name="ACME",
        employer_match_pattern="(?i)acme",
        version=1,
        is_active=True,
        fields=new_fields,
    )

    repo = SqlAlchemyTemplateRepository(mock_session)
    await repo.update_template("acme-v1", updated_dto)

    assert existing.fields[0].pdf_label_pattern == "(?i)^SUELDO\\s*BASE$"
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_deactivate_template_not_found_returns_none() -> None:
    """Test deactivate_template returns None for an unknown template_id."""
    mock_session = AsyncMock()
    mock_session.execute.return_value = _mock_result(scalar_one_or_none=None)

    repo = SqlAlchemyTemplateRepository(mock_session)
    result = await repo.deactivate_template("does-not-exist")

    assert result is None


@pytest.mark.asyncio
async def test_deactivate_template_flips_is_active() -> None:
    """Test deactivate_template sets is_active to False -- never a row DELETE."""
    existing = _build_model(is_active=True)
    mock_session = AsyncMock()
    mock_session.execute.side_effect = [
        _mock_result(scalar_one_or_none=existing),  # _get_model
        _mock_result(scalar_one_or_none=existing),  # get_template main select
        _mock_result(
            all=[_concepts_by_id_row()]
        ),  # get_template's _load_concepts_by_id
    ]

    repo = SqlAlchemyTemplateRepository(mock_session)
    await repo.deactivate_template("acme-v1")

    assert existing.is_active is False
    mock_session.commit.assert_awaited_once()
