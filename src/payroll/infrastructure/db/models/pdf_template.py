"""PDF template SQLAlchemy models (PAY_PDF_TEMPLATE / PAY_PDF_TEMPLATE_FIELD).

See `pf-db/alembic/versions/0009_pdf_template_tables.py` for the schema this
mirrors, and
`docs/proposals/pdf-template-management-design-recommendation.md` for why
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
from payroll.infrastructure.db.models.reference_data import (
    PayrollConceptKind,
    enum_values,
)
from sqlalchemy import Enum as SAEnum


class PdfTemplateModel(Base):
    """Represent a single versioned payroll PDF template."""

    __tablename__ = "PAY_PDF_TEMPLATE"

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[str] = mapped_column(String(80), unique=True)
    employer_id: Mapped[int | None] = mapped_column(
        ForeignKey("PAY_EMPLOYER.id"), nullable=True
    )
    employer_name: Mapped[str] = mapped_column(String(120))
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
    concept_code: Mapped[str] = mapped_column(
        String(40), ForeignKey("PAY_CONCEPT.code")
    )
    kind: Mapped[PayrollConceptKind] = mapped_column(
        SAEnum(
            PayrollConceptKind,
            name="payroll_concept_kind",
            native_enum=False,
            values_callable=enum_values,
        )
    )
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=Decimal("0.90"))

    template: Mapped[PdfTemplateModel] = relationship(back_populates="fields")
