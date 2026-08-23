"""add percentage allocation targets to investment strategies

Revision ID: 075
Revises: 074
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "075"
down_revision: Union[str, None] = "074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "investment_strategy_instruments",
        sa.Column("target_percentage", sa.Numeric(7, 4), nullable=True),
    )
    op.add_column(
        "investment_contribution_allocations",
        sa.Column("target_percentage", sa.Numeric(7, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("investment_contribution_allocations", "target_percentage")
    op.drop_column("investment_strategy_instruments", "target_percentage")
