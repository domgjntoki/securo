"""add an optional display currency to investment strategy classes

Revision ID: 076
Revises: 075
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "076"
down_revision: Union[str, None] = "075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "investment_strategy_classes",
        sa.Column("display_currency", sa.String(length=3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("investment_strategy_classes", "display_currency")
