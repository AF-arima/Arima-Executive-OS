from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

class CompanyStatus(str, Enum):
    PROSPECT = "prospect"
    ACTIVE = "active"
    CUSTOMER = "customer"
    PARTNER = "partner"
    INACTIVE = "inactive"


class ContactStatus(str, Enum):
    PROSPECT = "prospect"
    ACTIVE = "active"
    CUSTOMER = "customer"
    INACTIVE = "inactive"
    UNSUBSCRIBED = "unsubscribed"


class LeadSource(str, Enum):
    WEBSITE = "website"
    REFERRAL = "referral"
    LINKEDIN = "linkedin"
    EMAIL = "email"
    EVENT = "event"
    OUTBOUND = "outbound"
    PARTNER = "partner"
    ORGANIC = "organic"
    PAID = "paid"
    OTHER = "other"


class LeadStatus(str, Enum):
    NEW = "new"
    CONTACTED = "contacted"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    CONVERTED = "converted"
    LOST = "lost"
    DISQUALIFIED = "disqualified"


class DealStatus(str, Enum):
    OPEN = "open"
    WON = "won"
    LOST = "lost"


class CRMActivityType(str, Enum):
    CALL = "call"
    EMAIL = "email"
    MEETING = "meeting"
    TASK = "task"
    LINKEDIN = "linkedin"
    MESSAGE = "message"
    FOLLOW_UP = "follow_up"
    OTHER = "other"


def enum_column(enum_type: type[Enum], name: str) -> SQLAlchemyEnum:
    return SQLAlchemyEnum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda enum: [item.value for item in enum],
    )


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "crm_companies"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(253))
    industry: Mapped[str | None] = mapped_column(String(100))
    company_size: Mapped[str | None] = mapped_column(String(50))
    country: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CompanyStatus] = mapped_column(
        enum_column(CompanyStatus, "crm_company_status"),
        default=CompanyStatus.PROSPECT,
        nullable=False,
    )
    owner_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    contacts: Mapped[list[Contact]] = relationship(back_populates="company")
    leads: Mapped[list[Lead]] = relationship(back_populates="company")
    deals: Mapped[list[Deal]] = relationship(back_populates="company")

    __table_args__ = (
        Index("ix_crm_companies_owner_status", "owner_id", "status"),
        Index("ix_crm_companies_created_at", "created_at"),
        Index("ix_crm_companies_archived_at", "archived_at"),
        Index(
            "uq_crm_companies_domain",
            "domain",
            unique=True,
            postgresql_where=text("domain IS NOT NULL"),
            sqlite_where=text("domain IS NOT NULL"),
        ),
    )


class Contact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "crm_contacts"

    company_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("crm_companies.id", ondelete="SET NULL"),
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    country: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[ContactStatus] = mapped_column(
        enum_column(ContactStatus, "crm_contact_status"),
        default=ContactStatus.PROSPECT,
        nullable=False,
    )
    owner_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company | None] = relationship(back_populates="contacts")
    leads: Mapped[list[Lead]] = relationship(back_populates="contact")
    deals: Mapped[list[Deal]] = relationship(back_populates="primary_contact")

    __table_args__ = (
        Index("ix_crm_contacts_company_status", "company_id", "status"),
        Index("ix_crm_contacts_owner_status", "owner_id", "status"),
        Index("ix_crm_contacts_created_at", "created_at"),
        Index("ix_crm_contacts_archived_at", "archived_at"),
        Index(
            "uq_crm_contacts_email",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
            sqlite_where=text("email IS NOT NULL"),
        ),
    )


class Lead(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "crm_leads"

    company_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("crm_companies.id", ondelete="SET NULL"),
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("crm_contacts.id", ondelete="SET NULL"),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[LeadSource] = mapped_column(
        enum_column(LeadSource, "crm_lead_source"),
        nullable=False,
    )
    status: Mapped[LeadStatus] = mapped_column(
        enum_column(LeadStatus, "crm_lead_status"),
        default=LeadStatus.NEW,
        nullable=False,
    )
    score: Mapped[int | None] = mapped_column(Integer)
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="GBP", nullable=False)
    owner_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loss_reason: Mapped[str | None] = mapped_column(String(1000))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company | None] = relationship(back_populates="leads")
    contact: Mapped[Contact | None] = relationship(back_populates="leads")
    deals: Mapped[list[Deal]] = relationship(back_populates="originating_lead")

    __table_args__ = (
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)",
            name="score_range",
        ),
        CheckConstraint(
            "estimated_value IS NULL OR estimated_value >= 0",
            name="estimated_value_nonnegative",
        ),
        Index("ix_crm_leads_owner_status", "owner_id", "status"),
        Index("ix_crm_leads_company_id", "company_id"),
        Index("ix_crm_leads_contact_id", "contact_id"),
        Index("ix_crm_leads_next_follow_up_at", "next_follow_up_at"),
        Index("ix_crm_leads_created_at", "created_at"),
        Index("ix_crm_leads_archived_at", "archived_at"),
    )


class Pipeline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "crm_pipelines"

    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    stages: Mapped[list[PipelineStage]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineStage.position",
    )
    deals: Mapped[list[Deal]] = relationship(back_populates="pipeline")

    __table_args__ = (
        Index(
            "uq_crm_pipelines_default_active",
            "is_default",
            unique=True,
            postgresql_where=text("is_default AND is_active"),
            sqlite_where=text("is_default = 1 AND is_active = 1"),
        ),
    )


class PipelineStage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "crm_pipeline_stages"

    pipeline_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("crm_pipelines.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    probability: Mapped[int] = mapped_column(Integer, nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_won: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    pipeline: Mapped[Pipeline] = relationship(back_populates="stages")
    deals: Mapped[list[Deal]] = relationship(back_populates="stage")

    __table_args__ = (
        UniqueConstraint("pipeline_id", "position", name="uq_crm_pipeline_stages_pipeline_position"),
        UniqueConstraint("pipeline_id", "name", name="uq_crm_pipeline_stages_pipeline_name"),
        CheckConstraint(
            "probability >= 0 AND probability <= 100",
            name="probability_range",
        ),
        CheckConstraint("NOT is_won OR is_closed", name="won_is_closed"),
        Index(
            "uq_crm_pipeline_stages_won",
            "pipeline_id",
            unique=True,
            postgresql_where=text("is_won"),
            sqlite_where=text("is_won = 1"),
        ),
    )


class Deal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "crm_deals"

    pipeline_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("crm_pipelines.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stage_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("crm_pipeline_stages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    company_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("crm_companies.id", ondelete="SET NULL"),
    )
    primary_contact_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("crm_contacts.id", ondelete="SET NULL"),
    )
    originating_lead_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("crm_leads.id", ondelete="SET NULL"),
        unique=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="GBP", nullable=False)
    probability: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_close_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    actual_close_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[DealStatus] = mapped_column(
        enum_column(DealStatus, "crm_deal_status"),
        default=DealStatus.OPEN,
        nullable=False,
    )
    lost_reason: Mapped[str | None] = mapped_column(String(1000))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pipeline: Mapped[Pipeline] = relationship(back_populates="deals")
    stage: Mapped[PipelineStage] = relationship(back_populates="deals")
    company: Mapped[Company | None] = relationship(back_populates="deals")
    primary_contact: Mapped[Contact | None] = relationship(back_populates="deals")
    originating_lead: Mapped[Lead | None] = relationship(back_populates="deals")

    __table_args__ = (
        CheckConstraint("value >= 0", name="value_nonnegative"),
        CheckConstraint(
            "probability >= 0 AND probability <= 100",
            name="probability_range",
        ),
        Index("ix_crm_deals_pipeline_stage", "pipeline_id", "stage_id"),
        Index("ix_crm_deals_owner_status", "owner_id", "status"),
        Index("ix_crm_deals_company_id", "company_id"),
        Index("ix_crm_deals_primary_contact_id", "primary_contact_id"),
        Index("ix_crm_deals_expected_close_date", "expected_close_date"),
        Index("ix_crm_deals_created_at", "created_at"),
        Index("ix_crm_deals_archived_at", "archived_at"),
    )


class CRMNote(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "crm_notes"

    author_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    company_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_companies.id", ondelete="CASCADE")
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_contacts.id", ondelete="CASCADE")
    )
    lead_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_leads.id", ondelete="CASCADE")
    )
    deal_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_deals.id", ondelete="CASCADE")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN company_id IS NOT NULL THEN 1 ELSE 0 END + "
            "CASE WHEN contact_id IS NOT NULL THEN 1 ELSE 0 END + "
            "CASE WHEN lead_id IS NOT NULL THEN 1 ELSE 0 END + "
            "CASE WHEN deal_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="exactly_one_parent",
        ),
        Index("ix_crm_notes_company_id", "company_id"),
        Index("ix_crm_notes_contact_id", "contact_id"),
        Index("ix_crm_notes_lead_id", "lead_id"),
        Index("ix_crm_notes_deal_id", "deal_id"),
        Index("ix_crm_notes_author_created", "author_id", "created_at"),
    )


class CRMActivity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "crm_activities"

    type: Mapped[CRMActivityType] = mapped_column(
        enum_column(CRMActivityType, "crm_activity_type"),
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    company_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_companies.id", ondelete="SET NULL")
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_contacts.id", ondelete="SET NULL")
    )
    lead_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_leads.id", ondelete="SET NULL")
    )
    deal_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_deals.id", ondelete="SET NULL")
    )
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_to: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (
        CheckConstraint(
            "company_id IS NOT NULL OR contact_id IS NOT NULL OR "
            "lead_id IS NOT NULL OR deal_id IS NOT NULL",
            name="has_parent",
        ),
        Index("ix_crm_activities_company_id", "company_id"),
        Index("ix_crm_activities_contact_id", "contact_id"),
        Index("ix_crm_activities_lead_id", "lead_id"),
        Index("ix_crm_activities_deal_id", "deal_id"),
        Index("ix_crm_activities_actor_created", "actor_id", "created_at"),
        Index("ix_crm_activities_assigned_due", "assigned_to", "due_at"),
        Index("ix_crm_activities_type_created", "type", "created_at"),
    )
