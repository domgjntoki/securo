"""workspace-scoped investment strategy advisor

Revision ID: 074
Revises: 073
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "074"
down_revision: Union[str, None] = "073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "investment_strategies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("home_country", sa.String(2), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_investment_strategies_workspace_id", "investment_strategies", ["workspace_id"])

    op.create_table(
        "investment_strategy_wallets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("strategy_id", UUID, sa.ForeignKey("investment_strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_group_id", UUID, sa.ForeignKey("asset_groups.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("strategy_id", "asset_group_id"),
    )
    op.create_index("ix_investment_strategy_wallets_strategy_id", "investment_strategy_wallets", ["strategy_id"])
    op.create_index("ix_investment_strategy_wallets_asset_group_id", "investment_strategy_wallets", ["asset_group_id"])

    op.create_table(
        "investment_question_banks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("strategy_id", UUID, sa.ForeignKey("investment_strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_investment_question_banks_strategy_id", "investment_question_banks", ["strategy_id"])

    op.create_table(
        "investment_questions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("bank_id", UUID, sa.ForeignKey("investment_question_banks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(100), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_investment_questions_bank_id", "investment_questions", ["bank_id"])

    op.create_table(
        "investment_strategy_classes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("strategy_id", UUID, sa.ForeignKey("investment_strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_bank_id", UUID, sa.ForeignKey("investment_question_banks.id", ondelete="SET NULL")),
        sa.Column("template_key", sa.String(50)),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("target_percentage", sa.Numeric(7, 4), nullable=False, server_default="0"),
        sa.Column("scoring_mode", sa.String(20), nullable=False),
        sa.Column("purchase_mode", sa.String(30), nullable=False),
        sa.Column("quantity_decimals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_investment_strategy_classes_strategy_id", "investment_strategy_classes", ["strategy_id"])

    op.create_table(
        "investment_strategy_instruments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("strategy_id", UUID, sa.ForeignKey("investment_strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("class_id", UUID, sa.ForeignKey("investment_strategy_classes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("canonical_key", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("ticker", sa.String(32)),
        sa.Column("exchange", sa.String(64)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("isin", sa.String(20)),
        sa.Column("manual_price", sa.Numeric(18, 6)),
        sa.Column("cached_price", sa.Numeric(18, 6)),
        sa.Column("cached_price_at", sa.DateTime(timezone=True)),
        sa.Column("price_source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("manual_strength", sa.Integer()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("strategy_id", "canonical_key"),
    )
    op.create_index("ix_investment_strategy_instruments_strategy_id", "investment_strategy_instruments", ["strategy_id"])
    op.create_index("ix_investment_strategy_instruments_class_id", "investment_strategy_instruments", ["class_id"])

    op.create_table(
        "investment_instrument_asset_links",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("instrument_id", UUID, sa.ForeignKey("investment_strategy_instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", UUID, sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("instrument_id", "asset_id"),
    )
    op.create_index("ix_investment_instrument_asset_links_instrument_id", "investment_instrument_asset_links", ["instrument_id"])
    op.create_index("ix_investment_instrument_asset_links_asset_id", "investment_instrument_asset_links", ["asset_id"])

    op.create_table(
        "investment_instrument_answers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("instrument_id", UUID, sa.ForeignKey("investment_strategy_instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", UUID, sa.ForeignKey("investment_questions.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("instrument_id", "question_id"),
    )
    op.create_index("ix_investment_instrument_answers_instrument_id", "investment_instrument_answers", ["instrument_id"])
    op.create_index("ix_investment_instrument_answers_question_id", "investment_instrument_answers", ["question_id"])

    op.create_table(
        "investment_contribution_plans",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("strategy_id", UUID, sa.ForeignKey("investment_strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("contribution_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("portfolio_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("new_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("residual", sa.Numeric(18, 2), nullable=False),
        sa.Column("algorithm_version", sa.String(50), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_investment_contribution_plans_strategy_id", "investment_contribution_plans", ["strategy_id"])

    op.create_table(
        "investment_contribution_allocations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("plan_id", UUID, sa.ForeignKey("investment_contribution_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("instrument_id", UUID, sa.ForeignKey("investment_strategy_instruments.id", ondelete="SET NULL")),
        sa.Column("instrument_name", sa.String(255), nullable=False),
        sa.Column("class_name", sa.String(100), nullable=False),
        sa.Column("current_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("current_quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 6)),
        sa.Column("strength", sa.Integer()),
        sa.Column("suggested_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("suggested_quantity", sa.Numeric(24, 8)),
        sa.Column("after_percentage", sa.Numeric(9, 4), nullable=False),
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("actual_value", sa.Numeric(18, 2)),
        sa.Column("actual_quantity", sa.Numeric(24, 8)),
        sa.Column("execution_note", sa.String(500)),
    )
    op.create_index("ix_investment_contribution_allocations_plan_id", "investment_contribution_allocations", ["plan_id"])


def downgrade() -> None:
    for table in (
        "investment_contribution_allocations",
        "investment_contribution_plans",
        "investment_instrument_answers",
        "investment_instrument_asset_links",
        "investment_strategy_instruments",
        "investment_strategy_classes",
        "investment_questions",
        "investment_question_banks",
        "investment_strategy_wallets",
        "investment_strategies",
    ):
        op.drop_table(table)
