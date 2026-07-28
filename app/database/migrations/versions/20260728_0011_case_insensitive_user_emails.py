"""Normalize user email addresses and enforce case-insensitive uniqueness.

Revision ID: 20260728_0011
Revises: 20260728_0010
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0011"
down_revision: str | None = "20260728_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.scalar(
        sa.text(
            "SELECT lower(trim(email)) FROM users "
            "GROUP BY lower(trim(email)) HAVING count(*) > 1 LIMIT 1"
        )
    )
    if duplicate is not None:
        raise RuntimeError(
            "Cannot normalize user emails while case-insensitive duplicates "
            "exist; resolve duplicate accounts before retrying this migration."
        )
    op.execute(sa.text("UPDATE users SET email = lower(trim(email))"))
    op.create_index(
        "ux_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_users_email_lower", table_name="users")
