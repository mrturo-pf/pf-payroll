"""Reference-data SQLAlchemy models."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, Date, Enum as SAEnum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from payroll.domain.contributions import HealthInstitutionKind
from payroll.infrastructure.db.base import Base


def enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class ContributionCapType(StrEnum):
    PENSION_HEALTH = "pension_health"
    UNEMPLOYMENT = "unemployment"


class PayrollConceptKind(StrEnum):
    INCOME = "income"
    DISCOUNT = "discount"


class CurrencyModel(Base):
    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    is_fiat: Mapped[bool] = mapped_column(Boolean, default=True)
    unit_kind: Mapped[str] = mapped_column(String(20), default="currency")


class PensionInstitutionModel(Base):
    __tablename__ = "pension_institutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    mandatory_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0.10"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    plans: Mapped[list["PensionPlanModel"]] = relationship(back_populates="institution")


class HealthInstitutionModel(Base):
    __tablename__ = "health_institutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[HealthInstitutionKind] = mapped_column(
        SAEnum(HealthInstitutionKind, name="health_institution_kind", values_callable=enum_values)
    )
    mandatory_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0.07"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    plans: Mapped[list["HealthPlanModel"]] = relationship(back_populates="institution")


class PensionPlanModel(Base):
    __tablename__ = "pension_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("pension_institutions.id"))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    additional_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0"))

    institution: Mapped[PensionInstitutionModel] = relationship(back_populates="plans")


class HealthPlanModel(Base):
    __tablename__ = "health_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("health_institutions.id"))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    plan_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contracted_uf: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("0"))

    institution: Mapped[HealthInstitutionModel] = relationship(back_populates="plans")


class ContributionCapModel(Base):
    __tablename__ = "contribution_caps"

    id: Mapped[int] = mapped_column(primary_key=True)
    cap_type: Mapped[ContributionCapType] = mapped_column(
        SAEnum(ContributionCapType, name="contribution_cap_type", values_callable=enum_values)
    )
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    value_uf: Mapped[Decimal] = mapped_column(Numeric(10, 4))


class PayrollConceptModel(Base):
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
