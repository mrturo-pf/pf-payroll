"""Tests for the /payroll/templates PDF-template management routes."""

from dataclasses import replace

from fastapi.testclient import TestClient

from payroll.application.dto import PdfTemplateDTO, PdfTemplateFieldDTO
from payroll.application.errors import PayrollValidationError
from payroll.interfaces.api.dependencies import get_template_repository
from payroll.interfaces.api.main import app


class FakeTemplateRepository:
    """In-memory test double for TemplateRepository, keyed by template_id."""

    def __init__(self, templates: list[PdfTemplateDTO] | None = None) -> None:
        """Initialize the instance."""
        self._by_id: dict[str, PdfTemplateDTO] = {
            t.template_id: t for t in (templates or [])
        }

    async def list_templates(
        self, *, include_inactive: bool = False
    ) -> list[PdfTemplateDTO]:
        """List templates, active-only by default."""
        values = self._by_id.values()
        if not include_inactive:
            values = [t for t in values if t.is_active]
        return sorted(values, key=lambda t: t.template_id)

    async def get_template(
        self, template_id: str, *, include_inactive: bool = False
    ) -> PdfTemplateDTO | None:
        """Get one template by its external template_id, or None if not found."""
        template = self._by_id.get(template_id)
        if template is None or (not include_inactive and not template.is_active):
            return None
        return template

    async def create_template(self, template: PdfTemplateDTO) -> PdfTemplateDTO:
        """Create a new template. Raises PayrollValidationError on duplicates."""
        if template.template_id in self._by_id:
            raise PayrollValidationError("Duplicate template_id.")
        created = PdfTemplateDTO(
            id=len(self._by_id) + 1,
            template_id=template.template_id,
            employer_id=template.employer_id,
            employer_name=template.employer_name,
            employer_match_pattern=template.employer_match_pattern,
            version=template.version,
            is_active=True,
            fields=[replace(f, id=i) for i, f in enumerate(template.fields, start=1)],
        )
        self._by_id[template.template_id] = created
        return created

    async def update_template(
        self, template_id: str, template: PdfTemplateDTO
    ) -> PdfTemplateDTO | None:
        """Replace an existing template's fields in place."""
        existing = self._by_id.get(template_id)
        if existing is None:
            return None
        updated = PdfTemplateDTO(
            id=existing.id,
            template_id=template_id,
            employer_id=template.employer_id,
            employer_name=template.employer_name,
            employer_match_pattern=template.employer_match_pattern,
            version=template.version,
            is_active=existing.is_active,
            fields=[replace(f, id=i) for i, f in enumerate(template.fields, start=1)],
        )
        self._by_id[template_id] = updated
        return updated

    async def deactivate_template(self, template_id: str) -> PdfTemplateDTO | None:
        """Logically delete a template."""
        existing = self._by_id.get(template_id)
        if existing is None:
            return None
        deactivated = PdfTemplateDTO(
            id=existing.id,
            template_id=existing.template_id,
            employer_id=existing.employer_id,
            employer_name=existing.employer_name,
            employer_match_pattern=existing.employer_match_pattern,
            version=existing.version,
            is_active=False,
            fields=existing.fields,
        )
        self._by_id[template_id] = deactivated
        return deactivated

    async def resolve_concept_kinds(self, codes: set[str]) -> dict[str, str]:
        """Fake PAY_CONCEPT lookup -- a fixed, known-good map."""
        known = {"SALARY_BASE": "income", "INCOME_TAX": "discount"}
        return {code: known[code] for code in codes if code in known}


def _client() -> TestClient:
    return TestClient(app, headers={"X-API-Key": "test-key"})


def _get_template(repository: "FakeTemplateRepository", template_id: str):
    """Override the repository dependency and GET one template by id."""
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()
    try:
        return client.get(f"/payroll/templates/{template_id}")
    finally:
        app.dependency_overrides.clear()


def _sample_create_body(template_id: str = "acme-v1") -> dict:
    return {
        "template_id": template_id,
        "employer_name": "ACME",
        "employer_match_pattern": "(?i)acme",
        "version": 1,
        "fields": [
            {
                "pdf_label_pattern": "(?i)^SUELDO$",
                "concept_code": "SALARY_BASE",
                "confidence": 0.9,
            }
        ],
    }


def test_create_template_returns_201_with_body() -> None:
    """POST /payroll/templates creates and echoes the new template."""
    repository = FakeTemplateRepository()
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    try:
        response = client.post("/payroll/templates", json=_sample_create_body())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["template_id"] == "acme-v1"
    assert body["is_active"] is True
    assert len(body["fields"]) == 1
    assert body["fields"][0]["concept_code"] == "SALARY_BASE"


def test_create_template_rejects_invalid_regex_before_touching_repository() -> None:
    """An invalid pdf_label_pattern is a 422 from Pydantic, no repository call."""
    repository = FakeTemplateRepository()
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    body = _sample_create_body()
    body["fields"][0]["pdf_label_pattern"] = "(unclosed"

    try:
        response = client.post("/payroll/templates", json=body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_create_template_rejects_empty_fields_list() -> None:
    """A template with zero fields would never clear MIN_TEMPLATE_MATCH_SCORE."""
    repository = FakeTemplateRepository()
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    body = _sample_create_body()
    body["fields"] = []

    try:
        response = client.post("/payroll/templates", json=body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_create_template_rejects_unknown_concept_code() -> None:
    """A concept_code with no matching PAY_CONCEPT row is a 400, not a silent guess.

    This is the server-side half of the kind-derivation fix: the client no
    longer sends `kind` at all (see pf-db migration 0010), so the only way to
    reject a bad concept_code is resolving it -- never trusting a client-sent
    kind that could have silently contradicted it.
    """
    repository = FakeTemplateRepository()
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    body = _sample_create_body()
    body["fields"][0]["concept_code"] = "NOT_A_REAL_CODE"

    try:
        response = client.post("/payroll/templates", json=body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400


def test_create_template_rejects_missing_employer_name_and_id() -> None:
    """At least one of employer_name/employer_id is required (pf-db CHECK mirror)."""
    repository = FakeTemplateRepository()
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    body = _sample_create_body()
    del body["employer_name"]

    try:
        response = client.post("/payroll/templates", json=body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_create_template_duplicate_template_id_is_400() -> None:
    """A PayrollError from the repository (e.g. duplicate) maps to 400."""
    repository = FakeTemplateRepository(
        templates=[
            PdfTemplateDTO(
                id=1,
                template_id="acme-v1",
                employer_id=None,
                employer_name="ACME",
                employer_match_pattern="(?i)acme",
                version=1,
                is_active=True,
                fields=[],
            )
        ]
    )
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    try:
        response = client.post("/payroll/templates", json=_sample_create_body())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400


def test_list_templates_defaults_to_active_only() -> None:
    """GET /payroll/templates hides inactive templates unless asked."""
    active = PdfTemplateDTO(1, "active-v1", None, "A", "(?i)a", 1, True, [])
    inactive = PdfTemplateDTO(2, "inactive-v1", None, "B", "(?i)b", 1, False, [])
    repository = FakeTemplateRepository(templates=[active, inactive])
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    try:
        response = client.get("/payroll/templates")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    ids = [t["template_id"] for t in response.json()]
    assert ids == ["active-v1"]


def test_list_templates_include_inactive_shows_everything() -> None:
    """?include_inactive=true surfaces logically-deleted templates too."""
    active = PdfTemplateDTO(1, "active-v1", None, "A", "(?i)a", 1, True, [])
    inactive = PdfTemplateDTO(2, "inactive-v1", None, "B", "(?i)b", 1, False, [])
    repository = FakeTemplateRepository(templates=[active, inactive])
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    try:
        response = client.get("/payroll/templates?include_inactive=true")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    ids = sorted(t["template_id"] for t in response.json())
    assert ids == ["active-v1", "inactive-v1"]


def test_get_template_found() -> None:
    """GET /payroll/templates/{template_id} returns the matching template."""
    template = PdfTemplateDTO(1, "acme-v1", None, "ACME", "(?i)acme", 1, True, [])
    repository = FakeTemplateRepository(templates=[template])

    response = _get_template(repository, "acme-v1")

    assert response.status_code == 200
    assert response.json()["template_id"] == "acme-v1"


def test_get_template_includes_inactive() -> None:
    """GET by id must find a template even when it's been logically deleted."""
    template = PdfTemplateDTO(1, "acme-v1", None, "ACME", "(?i)acme", 1, False, [])
    repository = FakeTemplateRepository(templates=[template])

    response = _get_template(repository, "acme-v1")

    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_get_template_not_found_is_404() -> None:
    """GET /payroll/templates/{template_id} 404s for an unknown template_id."""
    repository = FakeTemplateRepository()
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    try:
        response = client.get("/payroll/templates/does-not-exist")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_update_template_replaces_fields() -> None:
    """PUT /payroll/templates/{template_id} mutates in place, no version bump needed."""
    template = PdfTemplateDTO(
        1,
        "acme-v1",
        None,
        "ACME",
        "(?i)acme",
        1,
        True,
        [PdfTemplateFieldDTO(1, "(?i)old", "SALARY_BASE", "income", 0.9)],
    )
    repository = FakeTemplateRepository(templates=[template])
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    body = _sample_create_body("acme-v1")
    body["fields"][0]["pdf_label_pattern"] = "(?i)new"

    try:
        response = client.put("/payroll/templates/acme-v1", json=body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["fields"][0]["pdf_label_pattern"] == "(?i)new"


def test_update_template_not_found_is_404() -> None:
    """PUT against an unknown template_id is a 404, never a silent create."""
    repository = FakeTemplateRepository()
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    try:
        response = client.put(
            "/payroll/templates/does-not-exist", json=_sample_create_body()
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


class FailingUpdateTemplateRepository:
    """Test double whose update_template() always raises a PayrollError."""

    async def resolve_concept_kinds(self, codes: set[str]) -> dict[str, str]:
        """Resolve everything OK -- the failure under test is update_template itself."""
        return {code: "income" for code in codes}

    async def update_template(
        self, template_id: str, template: PdfTemplateDTO
    ) -> PdfTemplateDTO | None:
        """Simulate a genuine application-level failure while updating."""
        raise PayrollValidationError("boom")


def test_update_template_maps_payroll_errors_to_400() -> None:
    """A PayrollError raised by the repository on update maps to 400, not 500."""
    app.dependency_overrides[get_template_repository] = lambda: (
        FailingUpdateTemplateRepository()
    )
    client = _client()

    try:
        response = client.put("/payroll/templates/acme-v1", json=_sample_create_body())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400


def test_deactivate_template_flips_is_active() -> None:
    """DELETE /payroll/templates/{template_id} is a logical delete, never a DELETE."""
    template = PdfTemplateDTO(1, "acme-v1", None, "ACME", "(?i)acme", 1, True, [])
    repository = FakeTemplateRepository(templates=[template])
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    try:
        response = client.delete("/payroll/templates/acme-v1")
        assert response.status_code == 200
        assert response.json()["is_active"] is False

        # The template must still be gettable by id afterwards -- it was
        # deactivated, not deleted.
        follow_up = client.get("/payroll/templates/acme-v1")
    finally:
        app.dependency_overrides.clear()

    assert follow_up.status_code == 200


def test_deactivate_template_not_found_is_404() -> None:
    """DELETE against an unknown template_id is a 404, not a silent no-op 200."""
    repository = FakeTemplateRepository()
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    try:
        response = client.delete("/payroll/templates/does-not-exist")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


def test_list_templates_does_not_collide_with_period_detail_route() -> None:
    """GET /payroll/templates must never be swallowed by GET /payroll/{period_id}.

    Registration order in interfaces/api/main.py matters here -- see that
    module's comment on why pdf_templates_router is included before
    payroll_router.
    """
    repository = FakeTemplateRepository()
    app.dependency_overrides[get_template_repository] = lambda: repository
    client = _client()

    try:
        response = client.get("/payroll/templates")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert isinstance(response.json(), list)
