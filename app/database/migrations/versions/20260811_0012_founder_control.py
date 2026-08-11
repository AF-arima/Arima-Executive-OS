"""Add founder-controlled data-feed provenance.

Revision ID: 20260811_0012
Revises: c0b73382a5a4
Create Date: 2026-08-11

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260811_0012"
down_revision: str | None = "c0b73382a5a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_AUDIT_ENTITY_VALUES = (
    "'project', 'task', 'company', 'contact', 'lead', 'pipeline', "
    "'pipeline_stage', 'deal', 'crm_note', 'crm_activity', 'mailbox', "
    "'email_template', 'email_draft', 'sequence', 'campaign', "
    "'automation'"
)
_AUDIT_ENTITY_VALUES_WITH_OBSERVATION = (
    f"{_AUDIT_ENTITY_VALUES}, 'data_feed_observation'"
)


def upgrade() -> None:
    op.create_table(
        "data_feed_observations",
        sa.Column("feed_key", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=500), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("entered_by_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entered_by_id"],
            ["users.id"],
            name=op.f("fk_data_feed_observations_entered_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_feed_observations")),
    )
    op.create_index(
        op.f("ix_data_feed_observations_entered_by_id"),
        "data_feed_observations",
        ["entered_by_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_data_feed_observations_correlation_id"),
        "data_feed_observations",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        "ix_data_feed_observations_feed_observed",
        "data_feed_observations",
        ["feed_key", "observed_at"],
        unique=False,
    )
    op.create_index(
        "ix_data_feed_observations_entered_created",
        "data_feed_observations",
        ["entered_by_id", "created_at"],
        unique=False,
    )

    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_entity", type_="check")
        batch.alter_column(
            "entity",
            existing_type=sa.String(length=14),
            type_=sa.String(length=21),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            "audit_entity",
            f"entity IN ({_AUDIT_ENTITY_VALUES_WITH_OBSERVATION})",
        )


def downgrade() -> None:
    # The prior enum cannot represent these rows or fit the longer value.
    op.execute(
        sa.text(
            "DELETE FROM audit_logs WHERE entity = 'data_feed_observation'"
        )
    )
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_entity", type_="check")
        batch.alter_column(
            "entity",
            existing_type=sa.String(length=21),
            type_=sa.String(length=14),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            "audit_entity",
            f"entity IN ({_AUDIT_ENTITY_VALUES})",
        )
    op.drop_table("data_feed_observations")
