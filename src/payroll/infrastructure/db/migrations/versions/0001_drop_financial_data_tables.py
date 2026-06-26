"""Drop financial data tables migrated to pf-rates.

Revision ID: 0001
Revises:
Create Date: 2026-06-25

exchange_rates, economic_indices, income_tax_brackets, and currencies are now
managed exclusively by the pf-rates microservice.  pf-payroll fetches this data
via the pf-rates REST API.
"""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop the four tables previously owned by pf-payroll."""
    # exchange_rates depends on currencies via FK — drop first
    op.drop_table("exchange_rates")
    op.drop_table("economic_indices")
    op.drop_table("income_tax_brackets")
    op.drop_table("currencies")


def downgrade() -> None:
    """Recreate the four tables with their original DDL."""
    op.create_table(
        "currencies",
        sa.Column("code", sa.String(3), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("is_fiat", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "unit_kind", sa.String(20), nullable=False, server_default="currency"
        ),
    )

    op.create_table(
        "exchange_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "currency_code",
            sa.String(3),
            sa.ForeignKey("currencies.code"),
            nullable=False,
        ),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("value_clp", sa.Numeric(18, 6), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("currency_code", "rate_date", name="uq_exchange_rate"),
    )

    op.create_table(
        "economic_indices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=False),
        sa.Column("index_value", sa.Numeric(12, 6), nullable=False),
        sa.Column("monthly_change", sa.Numeric(7, 4), nullable=True),
        sa.Column("yearly_change", sa.Numeric(7, 4), nullable=True),
        sa.Column(
            "base_period", sa.String(10), nullable=False, server_default="DIC-2018"
        ),
        sa.Column("source", sa.String(40), nullable=False, server_default="manual"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "code", "period_year", "period_month", name="uq_economic_index"
        ),
    )

    op.create_table(
        "income_tax_brackets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("lower_bound_utm", sa.Numeric(10, 4), nullable=False),
        sa.Column("upper_bound_utm", sa.Numeric(10, 4), nullable=True),
        sa.Column("marginal_rate", sa.Numeric(8, 6), nullable=False),
        sa.Column(
            "rebate_utm",
            sa.Numeric(10, 4),
            nullable=False,
            server_default="0",
        ),
        sa.UniqueConstraint(
            "valid_from", "lower_bound_utm", name="uq_income_tax_bracket"
        ),
    )
