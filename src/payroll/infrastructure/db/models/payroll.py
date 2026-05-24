"""Payroll SQLAlchemy models."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, Enum as SAEnum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from payroll.domain.contributions import EmploymentContractKind
from payroll.infrastructure.db.base import Base
from payroll.infrastructure.db.models.reference_data import (
    PayrollConceptModel,
    enum_values,
)


class PayrollStatus(StrEnum):
    """Represent Payroll Status."""

    PROJECTED = "projected"
    ACTUAL = "actual"
    REVIEWED = "reviewed"


class EmployerModel(Base):
    """Represent Employer Model."""

    __tablename__ = "employers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    tax_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), default="CL")
    started_at: Mapped[date] = mapped_column(Date)

    payroll_periods: Mapped[list["PayrollPeriodModel"]] = relationship(
        back_populates="employer"
    )


class PayrollPeriodModel(Base):
    """Represent Payroll Period Model."""

    __tablename__ = "payroll_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    employer_id: Mapped[int] = mapped_column(ForeignKey("employers.id"))
    period_year: Mapped[int]
    period_month: Mapped[int]
    payment_date: Mapped[date] = mapped_column(Date)
    worked_days: Mapped[int] = mapped_column(default=30)
    status: Mapped[PayrollStatus] = mapped_column(
        SAEnum(PayrollStatus, name="payroll_status", values_callable=enum_values),
        default=PayrollStatus.PROJECTED,
    )
    employment_contract_kind: Mapped[EmploymentContractKind] = mapped_column(
        SAEnum(
            EmploymentContractKind,
            name="employment_contract_kind",
            values_callable=enum_values,
        ),
        default=EmploymentContractKind.INDEFINITE,
    )
    declared_net_pay_clp: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    expected_net_pay_clp: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    net_pay_difference_clp: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    pension_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("pension_plans.id"), nullable=True
    )
    health_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("health_plans.id"), nullable=True
    )

    employer: Mapped[EmployerModel] = relationship(back_populates="payroll_periods")
    items: Mapped[list["PayrollItemModel"]] = relationship(back_populates="period")


class PayrollItemModel(Base):
    """Represent Payroll Item Model."""

    __tablename__ = "payroll_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(
        ForeignKey("payroll_periods.id", ondelete="CASCADE")
    )
    concept_id: Mapped[int] = mapped_column(ForeignKey("payroll_concepts.id"))
    amount_clp: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    notes: Mapped[str | None] = mapped_column(nullable=True)

    period: Mapped[PayrollPeriodModel] = relationship(back_populates="items")
    concept: Mapped[PayrollConceptModel] = relationship()


class PayrollSummaryModel(Base):
    """Represent Payroll Summary Model."""

    __tablename__ = "mv_payroll_summary"

    period_id: Mapped[int] = mapped_column(primary_key=True)
    employer_id: Mapped[int]
    period_year: Mapped[int]
    period_month: Mapped[int]
    payment_date: Mapped[date] = mapped_column(Date)
    taxable_income_clp: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    gross_income_clp: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    total_discounts_clp: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    net_pay_clp: Mapped[Decimal] = mapped_column(Numeric(18, 2))
