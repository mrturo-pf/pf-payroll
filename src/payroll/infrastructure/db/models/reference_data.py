"""Reference-data SQLAlchemy models."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    Enum as SAEnum,
    ForeignKey,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from payroll.domain.contributions import (
    HealthInstitutionKind,
    ComplementaryInsuranceCostType,
)
from payroll.infrastructure.db.base import Base


def enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Handle enum values."""
    return [member.value for member in enum_cls]


class ContributionCapType(StrEnum):
    """Represent Contribution Cap Type."""

    PENSION_HEALTH = "pension_health"
    UNEMPLOYMENT = "unemployment"


class PayrollConceptKind(StrEnum):
    """Represent Payroll Concept Kind."""

    INCOME = "income"
    DISCOUNT = "discount"


class PensionInstitutionModel(Base):
    """Represent Pension Institution Model."""

    __tablename__ = "pension_institutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    mandatory_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), default=Decimal("0.10")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    plans: Mapped[list["PensionPlanModel"]] = relationship(back_populates="institution")


class HealthInstitutionModel(Base):
    """Represent Health Institution Model."""

    __tablename__ = "health_institutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[HealthInstitutionKind] = mapped_column(
        SAEnum(
            HealthInstitutionKind,
            name="health_institution_kind",
            values_callable=enum_values,
        )
    )
    mandatory_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), default=Decimal("0.07")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    plans: Mapped[list["HealthPlanModel"]] = relationship(back_populates="institution")


class PensionPlanModel(Base):
    """Represent Pension Plan Model."""

    __tablename__ = "pension_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("pension_institutions.id"))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    additional_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), default=Decimal("0")
    )

    institution: Mapped[PensionInstitutionModel] = relationship(back_populates="plans")


class HealthPlanModel(Base):
    """Represent Health Plan Model."""

    __tablename__ = "health_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("health_institutions.id"))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    plan_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contracted_uf: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("0"))

    institution: Mapped[HealthInstitutionModel] = relationship(back_populates="plans")


class ContributionCapModel(Base):
    """Represent Contribution Cap Model."""

    __tablename__ = "contribution_caps"

    id: Mapped[int] = mapped_column(primary_key=True)
    cap_type: Mapped[ContributionCapType] = mapped_column(
        SAEnum(
            ContributionCapType,
            name="contribution_cap_type",
            values_callable=enum_values,
        )
    )
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    value_uf: Mapped[Decimal] = mapped_column(Numeric(10, 4))


class PayrollConceptModel(Base):
    """Represent Payroll Concept Model."""

    __tablename__ = "payroll_concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[PayrollConceptKind] = mapped_column(
        SAEnum(
            PayrollConceptKind,
            name="payroll_concept_kind",
            native_enum=False,
            values_callable=enum_values,
        )
    )
    is_taxable: Mapped[bool] = mapped_column(Boolean, default=False)


class ComplementaryInsuranceProviderModel(Base):
    """Represent Complementary Insurance Provider Model."""

    __tablename__ = "complementary_insurance_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class ComplementaryInsurancePlanModel(Base):
    """Represent Complementary Insurance Plan Model."""

    __tablename__ = "complementary_insurance_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("complementary_insurance_providers.id")
    )
    name: Mapped[str] = mapped_column(String(120))
    cost_type: Mapped[ComplementaryInsuranceCostType] = mapped_column(
        SAEnum(
            ComplementaryInsuranceCostType,
            name="complementary_insurance_cost_type",
            native_enum=False,
            values_callable=enum_values,
        )
    )
    cost_value: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    cost_currency: Mapped[str] = mapped_column(String(3), default="CLP")
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
