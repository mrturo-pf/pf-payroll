"""Unit tests for SqlAlchemyTemplateRepository."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from payroll.application.dto import PdfTemplateDTO, PdfTemplateFieldDTO
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
            concept_code="SALARY_BASE",
            kind=ModelPayrollConceptKind.INCOME,
            confidence=0.9,
        )
    ]
    return model


def _dto(*, is_active: bool = True) -> PdfTemplateDTO:
    """Build a PdfTemplateDTO matching _build_model()'s shape."""
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


def test_to_dto_maps_model_and_fields() -> None:
    """Test _to_dto maps a model (fields included) to the matching DTO."""
    dto = _to_dto(_build_model())
    assert dto == _dto()


@pytest.mark.asyncio
async def test_list_templates_returns_mapped_dtos() -> None:
    """Test list_templates maps every returned model."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [_build_model()]
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyTemplateRepository(mock_session)
    templates = await repo.list_templates()

    assert templates == [_dto()]


@pytest.mark.asyncio
async def test_list_active_templates_delegates_to_list_templates() -> None:
    """Test list_active_templates is a thin wrapper, not a second query shape."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [_build_model()]
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyTemplateRepository(mock_session)
    templates = await repo.list_active_templates()

    assert templates == [_dto()]


@pytest.mark.asyncio
async def test_get_template_found() -> None:
    """Test get_template returns the mapped DTO when found."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = _build_model()
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyTemplateRepository(mock_session)
    template = await repo.get_template("acme-v1")

    assert template == _dto()


@pytest.mark.asyncio
async def test_get_template_not_found_returns_none() -> None:
    """Test get_template returns None, never raises, when nothing matches."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyTemplateRepository(mock_session)
    template = await repo.get_template("does-not-exist")

    assert template is None


@pytest.mark.asyncio
async def test_create_template_commits_and_rereads() -> None:
    """Test create_template adds the model, commits, then re-reads it."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = _build_model()
    mock_session.execute.return_value = mock_result

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
    mock_session.commit.side_effect = IntegrityError("INSERT", {}, Exception("dup"))

    repo = SqlAlchemyTemplateRepository(mock_session)

    with pytest.raises(PayrollValidationError):
        await repo.create_template(_dto())

    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_template_not_found_returns_none() -> None:
    """Test update_template returns None (never raises) for an unknown template_id."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyTemplateRepository(mock_session)
    updated = await repo.update_template("does-not-exist", _dto())

    assert updated is None
    mock_session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_update_template_replaces_fields_in_place() -> None:
    """Test update_template mutates the existing model (no version bump)."""
    existing = _build_model()
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing
    mock_session.execute.return_value = mock_result

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
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyTemplateRepository(mock_session)
    result = await repo.deactivate_template("does-not-exist")

    assert result is None


@pytest.mark.asyncio
async def test_deactivate_template_flips_is_active() -> None:
    """Test deactivate_template sets is_active to False -- never a row DELETE."""
    existing = _build_model(is_active=True)
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing
    mock_session.execute.return_value = mock_result

    repo = SqlAlchemyTemplateRepository(mock_session)
    await repo.deactivate_template("acme-v1")

    assert existing.is_active is False
    mock_session.commit.assert_awaited_once()
