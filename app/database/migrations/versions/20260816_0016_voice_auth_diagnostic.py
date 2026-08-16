"""Add an audited Voice authorization diagnostic event.

Revision ID: 20260816_0016_voice_auth_diagnostic
Revises: 20260814_0015_ai
Create Date: 2026-08-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260816_0016_voice_auth_diagnostic"
down_revision: str | None = "20260814_0015_ai"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_AUDIT_ACTION_VALUES = (
    "'read', 'create', 'update', 'delete', 'assignment', 'status_change', "
    "'convert', 'stage_change', 'complete'"
)
_AUDIT_ACTION_VALUES_WITHOUT_READ = (
    "'create', 'update', 'delete', 'assignment', 'status_change', "
    "'convert', 'stage_change', 'complete'"
)
_AUDIT_ENTITY_VALUES = (
    "'project', 'task', 'company', 'contact', 'lead', 'pipeline', "
    "'pipeline_stage', 'deal', 'crm_note', 'crm_activity', 'mailbox', "
    "'email_template', 'email_draft', 'sequence', 'campaign', "
    "'automation', 'data_feed_observation'"
)
_AUDIT_ENTITY_VALUES_WITH_DIAGNOSTIC = (
    f"{_AUDIT_ENTITY_VALUES}, 'voice_authorization_diagnostic'"
)


def _audit_entity_type(*values: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name="audit_entity",
        native_enum=False,
        create_constraint=False,
    )


def upgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_action", type_="check")
        batch.drop_constraint("audit_entity", type_="check")
        batch.alter_column(
            "entity",
            existing_type=sa.String(length=21),
            type_=_audit_entity_type(
                "project",
                "task",
                "company",
                "contact",
                "lead",
                "pipeline",
                "pipeline_stage",
                "deal",
                "crm_note",
                "crm_activity",
                "mailbox",
                "email_template",
                "email_draft",
                "sequence",
                "campaign",
                "automation",
                "data_feed_observation",
                "voice_authorization_diagnostic",
            ),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            "audit_action",
            f"action IN ({_AUDIT_ACTION_VALUES})",
        )
        batch.create_check_constraint(
            "audit_entity",
            f"entity IN ({_AUDIT_ENTITY_VALUES_WITH_DIAGNOSTIC})",
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM audit_logs "
            "WHERE entity = 'voice_authorization_diagnostic'"
        )
    )
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_action", type_="check")
        batch.drop_constraint("audit_entity", type_="check")
        batch.alter_column(
            "entity",
            existing_type=_audit_entity_type(
                "project",
                "task",
                "company",
                "contact",
                "lead",
                "pipeline",
                "pipeline_stage",
                "deal",
                "crm_note",
                "crm_activity",
                "mailbox",
                "email_template",
                "email_draft",
                "sequence",
                "campaign",
                "automation",
                "data_feed_observation",
                "voice_authorization_diagnostic",
            ),
            type_=_audit_entity_type(
                "project",
                "task",
                "company",
                "contact",
                "lead",
                "pipeline",
                "pipeline_stage",
                "deal",
                "crm_note",
                "crm_activity",
                "mailbox",
                "email_template",
                "email_draft",
                "sequence",
                "campaign",
                "automation",
                "data_feed_observation",
            ),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            "audit_action",
            f"action IN ({_AUDIT_ACTION_VALUES_WITHOUT_READ})",
        )
        batch.create_check_constraint(
            "audit_entity",
            f"entity IN ({_AUDIT_ENTITY_VALUES})",
        )
