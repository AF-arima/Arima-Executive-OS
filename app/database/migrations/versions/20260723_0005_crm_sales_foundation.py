"""Add CRM and sales foundation.

Revision ID: 20260723_0005
Revises: 20260723_0004
Create Date: 2026-07-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0005"
down_revision: str | None = "20260723_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CRM_TABLES = (
    "crm_companies",
    "crm_contacts",
    "crm_leads",
    "crm_pipelines",
    "crm_pipeline_stages",
    "crm_deals",
    "crm_notes",
    "crm_activities",
)
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
CRM_METADATA = sa.MetaData(naming_convention=NAMING_CONVENTION)
sa.Table(
    "users",
    CRM_METADATA,
    sa.Column("id", sa.Uuid(), primary_key=True),
)


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
    )


def _identity_columns() -> tuple[sa.Column, sa.Column, sa.Column]:
    return (
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


crm_companies = sa.Table(
    "crm_companies",
    CRM_METADATA,
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("legal_name", sa.String(255)),
    sa.Column("website", sa.String(500)),
    sa.Column("domain", sa.String(253)),
    sa.Column("industry", sa.String(100)),
    sa.Column("company_size", sa.String(50)),
    sa.Column("country", sa.String(100)),
    sa.Column("city", sa.String(100)),
    sa.Column("address", sa.String(500)),
    sa.Column("description", sa.Text()),
    sa.Column(
        "status",
        _enum(
            "crm_company_status",
            "prospect",
            "active",
            "customer",
            "partner",
            "inactive",
        ),
        nullable=False,
    ),
    sa.Column(
        "owner_id",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
    ),
    sa.Column(
        "created_by",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    *_identity_columns(),
    sa.PrimaryKeyConstraint("id"),
)
sa.Index("ix_crm_companies_owner_status", crm_companies.c.owner_id, crm_companies.c.status)
sa.Index("ix_crm_companies_created_at", crm_companies.c.created_at)
sa.Index("ix_crm_companies_archived_at", crm_companies.c.archived_at)
sa.Index(
    "uq_crm_companies_domain",
    crm_companies.c.domain,
    unique=True,
    postgresql_where=sa.text("domain IS NOT NULL"),
    sqlite_where=sa.text("domain IS NOT NULL"),
)

crm_contacts = sa.Table(
    "crm_contacts",
    CRM_METADATA,
    sa.Column(
        "company_id",
        sa.Uuid(),
        sa.ForeignKey("crm_companies.id", ondelete="SET NULL"),
    ),
    sa.Column("first_name", sa.String(100), nullable=False),
    sa.Column("last_name", sa.String(100), nullable=False),
    sa.Column("job_title", sa.String(150)),
    sa.Column("email", sa.String(320)),
    sa.Column("phone", sa.String(50)),
    sa.Column("linkedin_url", sa.String(500)),
    sa.Column("country", sa.String(100)),
    sa.Column("city", sa.String(100)),
    sa.Column(
        "status",
        _enum(
            "crm_contact_status",
            "prospect",
            "active",
            "customer",
            "inactive",
            "unsubscribed",
        ),
        nullable=False,
    ),
    sa.Column(
        "owner_id",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
    ),
    sa.Column(
        "created_by",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    *_identity_columns(),
    sa.PrimaryKeyConstraint("id"),
)
sa.Index("ix_crm_contacts_company_status", crm_contacts.c.company_id, crm_contacts.c.status)
sa.Index("ix_crm_contacts_owner_status", crm_contacts.c.owner_id, crm_contacts.c.status)
sa.Index("ix_crm_contacts_created_at", crm_contacts.c.created_at)
sa.Index("ix_crm_contacts_archived_at", crm_contacts.c.archived_at)
sa.Index(
    "uq_crm_contacts_email",
    crm_contacts.c.email,
    unique=True,
    postgresql_where=sa.text("email IS NOT NULL"),
    sqlite_where=sa.text("email IS NOT NULL"),
)

crm_leads = sa.Table(
    "crm_leads",
    CRM_METADATA,
    sa.Column(
        "company_id",
        sa.Uuid(),
        sa.ForeignKey("crm_companies.id", ondelete="SET NULL"),
    ),
    sa.Column(
        "contact_id",
        sa.Uuid(),
        sa.ForeignKey("crm_contacts.id", ondelete="SET NULL"),
    ),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column(
        "source",
        _enum(
            "crm_lead_source",
            "website",
            "referral",
            "linkedin",
            "email",
            "event",
            "outbound",
            "partner",
            "organic",
            "paid",
            "other",
        ),
        nullable=False,
    ),
    sa.Column(
        "status",
        _enum(
            "crm_lead_status",
            "new",
            "contacted",
            "engaged",
            "qualified",
            "converted",
            "lost",
            "disqualified",
        ),
        nullable=False,
    ),
    sa.Column("score", sa.Integer()),
    sa.Column("estimated_value", sa.Numeric(18, 2)),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column(
        "owner_id",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
    ),
    sa.Column(
        "created_by",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("last_contacted_at", sa.DateTime(timezone=True)),
    sa.Column("next_follow_up_at", sa.DateTime(timezone=True)),
    sa.Column("qualified_at", sa.DateTime(timezone=True)),
    sa.Column("converted_at", sa.DateTime(timezone=True)),
    sa.Column("lost_at", sa.DateTime(timezone=True)),
    sa.Column("loss_reason", sa.String(1000)),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    *_identity_columns(),
    sa.PrimaryKeyConstraint("id"),
    sa.CheckConstraint(
        "score IS NULL OR (score >= 0 AND score <= 100)",
        name="score_range",
    ),
    sa.CheckConstraint(
        "estimated_value IS NULL OR estimated_value >= 0",
        name="estimated_value_nonnegative",
    ),
)
sa.Index("ix_crm_leads_owner_status", crm_leads.c.owner_id, crm_leads.c.status)
sa.Index("ix_crm_leads_company_id", crm_leads.c.company_id)
sa.Index("ix_crm_leads_contact_id", crm_leads.c.contact_id)
sa.Index("ix_crm_leads_next_follow_up_at", crm_leads.c.next_follow_up_at)
sa.Index("ix_crm_leads_created_at", crm_leads.c.created_at)
sa.Index("ix_crm_leads_archived_at", crm_leads.c.archived_at)

crm_pipelines = sa.Table(
    "crm_pipelines",
    CRM_METADATA,
    sa.Column("name", sa.String(150), nullable=False, unique=True),
    sa.Column("description", sa.String(1000)),
    sa.Column("is_default", sa.Boolean(), nullable=False),
    sa.Column("is_active", sa.Boolean(), nullable=False),
    sa.Column(
        "created_by",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    *_identity_columns(),
    sa.PrimaryKeyConstraint("id"),
)
sa.Index(
    "uq_crm_pipelines_default_active",
    crm_pipelines.c.is_default,
    unique=True,
    postgresql_where=sa.text("is_default AND is_active"),
    sqlite_where=sa.text("is_default = 1 AND is_active = 1"),
)

crm_pipeline_stages = sa.Table(
    "crm_pipeline_stages",
    CRM_METADATA,
    sa.Column(
        "pipeline_id",
        sa.Uuid(),
        sa.ForeignKey("crm_pipelines.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("name", sa.String(100), nullable=False),
    sa.Column("position", sa.Integer(), nullable=False),
    sa.Column("probability", sa.Integer(), nullable=False),
    sa.Column("is_closed", sa.Boolean(), nullable=False),
    sa.Column("is_won", sa.Boolean(), nullable=False),
    *_identity_columns(),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("pipeline_id", "position"),
    sa.UniqueConstraint("pipeline_id", "name"),
    sa.CheckConstraint(
        "probability >= 0 AND probability <= 100",
        name="probability_range",
    ),
    sa.CheckConstraint("NOT is_won OR is_closed", name="won_is_closed"),
)
sa.Index(
    "uq_crm_pipeline_stages_won",
    crm_pipeline_stages.c.pipeline_id,
    unique=True,
    postgresql_where=sa.text("is_won"),
    sqlite_where=sa.text("is_won = 1"),
)

crm_deals = sa.Table(
    "crm_deals",
    CRM_METADATA,
    sa.Column(
        "pipeline_id",
        sa.Uuid(),
        sa.ForeignKey("crm_pipelines.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "stage_id",
        sa.Uuid(),
        sa.ForeignKey("crm_pipeline_stages.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "company_id",
        sa.Uuid(),
        sa.ForeignKey("crm_companies.id", ondelete="SET NULL"),
    ),
    sa.Column(
        "primary_contact_id",
        sa.Uuid(),
        sa.ForeignKey("crm_contacts.id", ondelete="SET NULL"),
    ),
    sa.Column(
        "originating_lead_id",
        sa.Uuid(),
        sa.ForeignKey("crm_leads.id", ondelete="SET NULL"),
        unique=True,
    ),
    sa.Column("title", sa.String(255), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column("value", sa.Numeric(18, 2), nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("probability", sa.Integer(), nullable=False),
    sa.Column("expected_close_date", sa.DateTime(timezone=True)),
    sa.Column("actual_close_date", sa.DateTime(timezone=True)),
    sa.Column(
        "owner_id",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "created_by",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "status",
        _enum("crm_deal_status", "open", "won", "lost"),
        nullable=False,
    ),
    sa.Column("lost_reason", sa.String(1000)),
    sa.Column("archived_at", sa.DateTime(timezone=True)),
    *_identity_columns(),
    sa.PrimaryKeyConstraint("id"),
    sa.CheckConstraint("value >= 0", name="value_nonnegative"),
    sa.CheckConstraint(
        "probability >= 0 AND probability <= 100",
        name="probability_range",
    ),
)
sa.Index("ix_crm_deals_pipeline_stage", crm_deals.c.pipeline_id, crm_deals.c.stage_id)
sa.Index("ix_crm_deals_owner_status", crm_deals.c.owner_id, crm_deals.c.status)
sa.Index("ix_crm_deals_company_id", crm_deals.c.company_id)
sa.Index("ix_crm_deals_primary_contact_id", crm_deals.c.primary_contact_id)
sa.Index("ix_crm_deals_expected_close_date", crm_deals.c.expected_close_date)
sa.Index("ix_crm_deals_created_at", crm_deals.c.created_at)
sa.Index("ix_crm_deals_archived_at", crm_deals.c.archived_at)

crm_notes = sa.Table(
    "crm_notes",
    CRM_METADATA,
    sa.Column(
        "author_id",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "company_id",
        sa.Uuid(),
        sa.ForeignKey("crm_companies.id", ondelete="CASCADE"),
    ),
    sa.Column(
        "contact_id",
        sa.Uuid(),
        sa.ForeignKey("crm_contacts.id", ondelete="CASCADE"),
    ),
    sa.Column(
        "lead_id",
        sa.Uuid(),
        sa.ForeignKey("crm_leads.id", ondelete="CASCADE"),
    ),
    sa.Column(
        "deal_id",
        sa.Uuid(),
        sa.ForeignKey("crm_deals.id", ondelete="CASCADE"),
    ),
    sa.Column("body", sa.Text(), nullable=False),
    *_identity_columns(),
    sa.PrimaryKeyConstraint("id"),
    sa.CheckConstraint(
        "(CASE WHEN company_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN contact_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN lead_id IS NOT NULL THEN 1 ELSE 0 END + "
        "CASE WHEN deal_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
        name="exactly_one_parent",
    ),
)
sa.Index("ix_crm_notes_company_id", crm_notes.c.company_id)
sa.Index("ix_crm_notes_contact_id", crm_notes.c.contact_id)
sa.Index("ix_crm_notes_lead_id", crm_notes.c.lead_id)
sa.Index("ix_crm_notes_deal_id", crm_notes.c.deal_id)
sa.Index("ix_crm_notes_author_created", crm_notes.c.author_id, crm_notes.c.created_at)

crm_activities = sa.Table(
    "crm_activities",
    CRM_METADATA,
    sa.Column(
        "type",
        _enum(
            "crm_activity_type",
            "call",
            "email",
            "meeting",
            "task",
            "linkedin",
            "message",
            "follow_up",
            "other",
        ),
        nullable=False,
    ),
    sa.Column("subject", sa.String(255), nullable=False),
    sa.Column("description", sa.Text()),
    sa.Column(
        "company_id",
        sa.Uuid(),
        sa.ForeignKey("crm_companies.id", ondelete="SET NULL"),
    ),
    sa.Column(
        "contact_id",
        sa.Uuid(),
        sa.ForeignKey("crm_contacts.id", ondelete="SET NULL"),
    ),
    sa.Column(
        "lead_id",
        sa.Uuid(),
        sa.ForeignKey("crm_leads.id", ondelete="SET NULL"),
    ),
    sa.Column(
        "deal_id",
        sa.Uuid(),
        sa.ForeignKey("crm_deals.id", ondelete="SET NULL"),
    ),
    sa.Column(
        "actor_id",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "assigned_to",
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
    ),
    sa.Column("occurred_at", sa.DateTime(timezone=True)),
    sa.Column("due_at", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    sa.Column("outcome", sa.String(1000)),
    *_identity_columns(),
    sa.PrimaryKeyConstraint("id"),
    sa.CheckConstraint(
        "company_id IS NOT NULL OR contact_id IS NOT NULL OR "
        "lead_id IS NOT NULL OR deal_id IS NOT NULL",
        name="has_parent",
    ),
)
sa.Index("ix_crm_activities_company_id", crm_activities.c.company_id)
sa.Index("ix_crm_activities_contact_id", crm_activities.c.contact_id)
sa.Index("ix_crm_activities_lead_id", crm_activities.c.lead_id)
sa.Index("ix_crm_activities_deal_id", crm_activities.c.deal_id)
sa.Index(
    "ix_crm_activities_actor_created",
    crm_activities.c.actor_id,
    crm_activities.c.created_at,
)
sa.Index(
    "ix_crm_activities_assigned_due",
    crm_activities.c.assigned_to,
    crm_activities.c.due_at,
)
sa.Index(
    "ix_crm_activities_type_created",
    crm_activities.c.type,
    crm_activities.c.created_at,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in CRM_TABLES:
        CRM_METADATA.tables[table_name].create(bind=bind, checkfirst=False)

    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_action", type_="check")
        batch.drop_constraint("audit_entity", type_="check")
        batch.alter_column(
            "entity",
            existing_type=sa.String(length=7),
            type_=sa.String(length=14),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            "audit_action",
            "action IN ('create', 'update', 'delete', 'assignment', "
            "'status_change', 'convert', 'stage_change', 'complete')",
        )
        batch.create_check_constraint(
            "audit_entity",
            "entity IN ('project', 'task', 'company', 'contact', 'lead', "
            "'pipeline', 'pipeline_stage', 'deal', 'crm_note', "
            "'crm_activity')",
        )
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("notification_type", type_="check")
        batch.create_check_constraint(
            "notification_type",
            "type IN ('task_assigned', 'task_due_soon', 'task_overdue', "
            "'project_status_changed', 'system', 'lead_assigned', "
            "'deal_assigned', 'deal_stage_changed', 'lead_qualified', "
            "'lead_converted', 'follow_up_due', "
            "'crm_activity_assigned')",
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("notification_type", type_="check")
        batch.create_check_constraint(
            "notification_type",
            "type IN ('task_assigned', 'task_due_soon', 'task_overdue', "
            "'project_status_changed', 'system')",
        )
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_action", type_="check")
        batch.drop_constraint("audit_entity", type_="check")
        batch.alter_column(
            "entity",
            existing_type=sa.String(length=14),
            type_=sa.String(length=7),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            "audit_action",
            "action IN ('create', 'update', 'delete', 'assignment', "
            "'status_change')",
        )
        batch.create_check_constraint(
            "audit_entity",
            "entity IN ('project', 'task')",
        )

    bind = op.get_bind()
    for table_name in reversed(CRM_TABLES):
        CRM_METADATA.tables[table_name].drop(bind=bind, checkfirst=False)
