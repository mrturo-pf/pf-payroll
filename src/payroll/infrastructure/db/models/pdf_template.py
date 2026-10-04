"""PDF template SQLAlchemy models (PAY_PDF_TEMPLATE / PAY_PDF_TEMPLATE_FIELD).

See `pf-db/alembic/versions/0009_pdf_template_tables.py` for the schema this
mirrors, and
`docs/proposals/pdf-template-management-recommendation.md` for why
these tables replaced the former git-tracked JSON template files.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from payroll.infrastructure.db.base import Base


class PdfTemplateModel(Base):
    """Represent a single versioned payroll PDF template."""

    __tablename__ = "PAY_PDF_TEMPLATE"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[str] = mapped_column(String(80), unique=True)
    # Required: a template may only be created for an employer that already
    # has a PAY_EMPLOYER row (i.e. after its first payroll import has run at
    # least once). There is deliberately no employer_name column -- it used
    # to be a literal-override fallback for when employer_id was nullable
    # (see pf-db migration 0010), but that case can no longer happen. The
    # repository always resolves the display name fresh from PAY_EMPLOYER via
    # employer_id on every read. See pf-db migration 0012.
    employer_id: Mapped[int] = mapped_column(ForeignKey("PAY_EMPLOYER.id"))
    employer_match_pattern: Mapped[str] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    fields: Mapped[list["PdfTemplateFieldModel"]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="PdfTemplateFieldModel.id",
    )


class PdfTemplateFieldModel(Base):
    """Represent one label-to-concept mapping rule within a PdfTemplateModel."""

    __tablename__ = "PAY_PDF_TEMPLATE_FIELD"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("PAY_PDF_TEMPLATE.id", ondelete="CASCADE")
    )
    pdf_label_pattern: Mapped[str] = mapped_column(String(500))
    # No `kind` column -- it would duplicate PAY_CONCEPT.kind with no
    # referential integrity tying the two copies together. The repository
    # always resolves kind from PAY_CONCEPT via concept_id at read time. See
    # pf-db migration 0010.
    # concept_id, not concept_code: every other table referencing PAY_CONCEPT
    # does so by its surrogate id (PAY_ITEM.concept_id) -- a VARCHAR FK to
    # PAY_CONCEPT(code) was an unintentional inconsistency, fixed in pf-db
    # migration 0011. concept_code remains the business-facing identifier at
    # every application layer above this model (DTOs, requests/responses,
    # the PDF-matching engine); only this storage column changed, resolved
    # at the repository boundary exactly like kind already is.
    concept_id: Mapped[int] = mapped_column(ForeignKey("PAY_CONCEPT.id"))
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=Decimal("0.90"))

    template: Mapped[PdfTemplateModel] = relationship(back_populates="fields")
