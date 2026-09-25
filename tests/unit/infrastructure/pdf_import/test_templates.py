"""Tests for payroll PDF template loading and matching."""

import json
from pathlib import Path

import pytest

from payroll.infrastructure.pdf_import.templates import (
    MIN_TEMPLATE_MATCH_SCORE,
    Template,
    load_templates,
    match_field,
    select_template,
)


def _write_template(directory: Path, name: str, payload: dict) -> Path:
    """Write a template JSON payload to disk and return its path."""
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _acme_payload(version: int = 1, fields: list[dict] | None = None) -> dict:
    """Build a minimal, valid template payload for tests."""
    return {
        "template_id": f"acme-v{version}",
        "employer_name": "ACME",
        "version": version,
        "employer_match": {"name_pattern": "(?i)acme"},
        "fields": fields
        if fields is not None
        else [
            {
                "pdf_label_pattern": "(?i)^SUELDO$",
                "concept_code": "SALARY_BASE",
                "kind": "income",
                "confidence": 0.9,
            },
            {
                "pdf_label_pattern": "(?i)IMPUESTO",
                "concept_code": "INCOME_TAX",
                "kind": "discount",
                "confidence": 0.9,
            },
            {
                "pdf_label_pattern": "(?i)SALUD",
                "concept_code": "HEALTH_BASE",
                "kind": "discount",
                "confidence": 0.6,
            },
        ],
    }


class TestLoadTemplates:
    """Tests for load_templates."""

    def test_loads_and_compiles_every_json_file_recursively(
        self, tmp_path: Path
    ) -> None:
        """Test loads and compiles every json file recursively."""
        _write_template(tmp_path / "acme", "v1.json", _acme_payload())
        templates = load_templates(tmp_path)
        assert len(templates) == 1
        template = templates[0]
        assert isinstance(template, Template)
        assert template.template_id == "acme-v1"
        assert template.employer_name == "ACME"
        assert template.version == 1
        assert len(template.fields) == 3

    def test_defaults_confidence_when_omitted(self, tmp_path: Path) -> None:
        """Test defaults confidence when omitted."""
        payload = _acme_payload(
            fields=[
                {
                    "pdf_label_pattern": "(?i)^SUELDO$",
                    "concept_code": "SALARY_BASE",
                    "kind": "income",
                }
            ]
        )
        _write_template(tmp_path / "acme", "v1.json", payload)
        template = load_templates(tmp_path)[0]
        assert template.fields[0].confidence == 0.9

    def test_returns_empty_list_when_directory_missing(self, tmp_path: Path) -> None:
        """Test returns empty list when directory missing."""
        assert load_templates(tmp_path / "does-not-exist") == []

    def test_uses_default_templates_directory_when_none_given(self) -> None:
        """The shipped walmart-chile template loads via the default path."""
        templates = load_templates()
        assert any(t.template_id == "walmart-chile-v1" for t in templates)


class TestSelectTemplate:
    """Tests for select_template."""

    @pytest.fixture
    def templates(self, tmp_path: Path) -> list[Template]:
        """Load a v1/v2 ACME template pair for scoring tests."""
        _write_template(tmp_path / "acme", "v1.json", _acme_payload(version=1))
        _write_template(
            tmp_path / "acme",
            "v2.json",
            _acme_payload(
                version=2,
                fields=[
                    {
                        "pdf_label_pattern": "(?i)^SUELDO$",
                        "concept_code": "SALARY_BASE",
                        "kind": "income",
                    },
                    {
                        "pdf_label_pattern": "(?i)IMPUESTO",
                        "concept_code": "INCOME_TAX",
                        "kind": "discount",
                    },
                    {
                        "pdf_label_pattern": "(?i)SALUD",
                        "concept_code": "HEALTH_BASE",
                        "kind": "discount",
                    },
                    {
                        "pdf_label_pattern": "(?i)GRATIFICACION",
                        "concept_code": "LEGAL_GRATUITY",
                        "kind": "income",
                    },
                ],
            ),
        )
        return load_templates(tmp_path)

    def test_returns_none_when_no_employer_matches(
        self, templates: list[Template]
    ) -> None:
        """Test returns none when no employer matches."""
        assert select_template(templates, "some other company", ["SUELDO"]) is None

    def test_returns_none_when_score_below_threshold(
        self, templates: list[Template]
    ) -> None:
        """Test returns none when score below threshold."""
        assert MIN_TEMPLATE_MATCH_SCORE == 3
        result = select_template(templates, "ACME S.A.", ["SUELDO"])
        assert result is None

    def test_picks_highest_scoring_candidate(self, templates: list[Template]) -> None:
        """v2 matches one extra label (GRATIFICACION), so it should win."""
        labels = ["SUELDO", "IMPUESTO", "SALUD BASE", "GRATIFICACION LEGAL"]
        result = select_template(templates, "ACME S.A.", labels)
        assert result is not None
        assert result.version == 2

    def test_picks_only_candidate_when_scores_tie(self, tmp_path: Path) -> None:
        """Test picks only candidate when scores tie."""
        _write_template(tmp_path / "acme", "v1.json", _acme_payload(version=1))
        templates = load_templates(tmp_path)
        labels = ["SUELDO", "IMPUESTO", "SALUD BASE"]
        result = select_template(templates, "ACME S.A.", labels)
        assert result is not None
        assert result.template_id == "acme-v1"


class TestMatchField:
    """Tests for match_field."""

    def test_returns_first_matching_field(self, tmp_path: Path) -> None:
        """Test returns first matching field."""
        _write_template(tmp_path / "acme", "v1.json", _acme_payload())
        template = load_templates(tmp_path)[0]
        field = match_field(template, "SUELDO")
        assert field is not None
        assert field.concept_code == "SALARY_BASE"

    def test_returns_none_when_nothing_matches(self, tmp_path: Path) -> None:
        """Test returns none when nothing matches."""
        _write_template(tmp_path / "acme", "v1.json", _acme_payload())
        template = load_templates(tmp_path)[0]
        assert match_field(template, "UNKNOWN LABEL") is None
