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


class EmployerPaymentDateRule(StrEnum):
    """Represent supported employer payment-date rules."""

    LAST_BUSINESS_DAY_OF_MONTH = "last_business_day_of_month"
    FIXED_DAY_OF_MONTH = "fixed_day_of_month"
    CALENDAR_DAYS_BEFORE_END_OF_MONTH = "calendar_days_before_end_of_month"


class EmployerFixedDayRoll(StrEnum):
    """Represent fixed-day fallback behavior for non-business dates."""

    PREVIOUS_BUSINESS_DAY = "previous_business_day"
    NEXT_BUSINESS_DAY = "next_business_day"


class EmployerModel(Base):
    """Represent Employer Model."""

    __tablename__ = "employers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    tax_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), default="CL")
    started_at: Mapped[date] = mapped_column(Date)
    ended_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_date_rule: Mapped[EmployerPaymentDateRule] = mapped_column(
        SAEnum(
            EmployerPaymentDateRule,
            name="employer_payment_date_rule",
            values_callable=enum_values,
        ),
        default=EmployerPaymentDateRule.LAST_BUSINESS_DAY_OF_MONTH,
    )
    payment_month_offset: Mapped[int] = mapped_column(default=0)
    payment_day_of_month: Mapped[int | None] = mapped_column(nullable=True)
    payment_business_day_offset: Mapped[int] = mapped_column(default=0)
    payment_calendar_day_offset: Mapped[int] = mapped_column(default=0)
    payment_effective_on_processing_next_day: Mapped[bool] = mapped_column(
        default=False
    )
    payment_fixed_day_roll: Mapped[EmployerFixedDayRoll] = mapped_column(
        SAEnum(
            EmployerFixedDayRoll,
            name="employer_fixed_day_roll",
            values_callable=enum_values,
        ),
        default=EmployerFixedDayRoll.PREVIOUS_BUSINESS_DAY,
    )

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
    health_plans: Mapped[list["PayrollPeriodHealthPlanModel"]] = relationship(
        back_populates="period",
        cascade="all, delete-orphan",
    )


class PayrollPeriodHealthPlanModel(Base):
    """Represent health plan snapshots assigned to a payroll period."""

    __tablename__ = "payroll_period_health_plans"

    period_id: Mapped[int] = mapped_column(
        ForeignKey("payroll_periods.id", ondelete="CASCADE"),
        primary_key=True,
    )
    health_plan_id: Mapped[int] = mapped_column(
        ForeignKey("health_plans.id"),
        primary_key=True,
    )

    period: Mapped[PayrollPeriodModel] = relationship(back_populates="health_plans")


class PayrollComplementaryInsuranceModel(Base):
    """Represent complementary insurance plans assigned to a payroll period."""

    __tablename__ = "payroll_complementary_insurance"

    period_id: Mapped[int] = mapped_column(
        ForeignKey("payroll_periods.id", ondelete="CASCADE"),
        primary_key=True,
    )
    complementary_insurance_plan_id: Mapped[int] = mapped_column(
        ForeignKey("complementary_insurance_plans.id"),
        primary_key=True,
    )


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
