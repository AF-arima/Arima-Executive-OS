import asyncio
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.database.models import (
    CRMActivity,
    CRMActivityType,
    CRMNote,
    Company,
    Contact,
    Deal,
    DealStatus,
    Lead,
    LeadSource,
    Pipeline,
    PipelineStage,
    User,
)
from tests.database.helpers import sqlite_session


def user() -> User:
    return User(
        email="crm-model@example.com",
        hashed_password="hash",
        first_name="CRM",
        last_name="Owner",
    )


def test_crm_relationships_and_constraints() -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            owner = user()
            session.add(owner)
            await session.flush()
            company = Company(
                name="Arima",
                domain="arima.example",
                owner_id=owner.id,
                created_by=owner.id,
            )
            contact = Contact(
                first_name="Ada",
                last_name="Lovelace",
                email="ada@example.com",
                company=company,
                owner_id=owner.id,
                created_by=owner.id,
            )
            lead = Lead(
                title="Opportunity",
                source=LeadSource.REFERRAL,
                company=company,
                contact=contact,
                owner_id=owner.id,
                created_by=owner.id,
            )
            pipeline = Pipeline(name="Sales", created_by=owner.id)
            stage = PipelineStage(
                pipeline=pipeline,
                name="Discovery",
                position=0,
                probability=25,
            )
            session.add_all([company, contact, lead, pipeline])
            await session.flush()
            deal = Deal(
                pipeline=pipeline,
                stage=stage,
                title="Opportunity",
                value=Decimal("1000"),
                probability=25,
                owner_id=owner.id,
                created_by=owner.id,
                status=DealStatus.OPEN,
                originating_lead=lead,
            )
            session.add(deal)
            await session.flush()
            note = CRMNote(author_id=owner.id, deal_id=deal.id, body="Safe note")
            activity = CRMActivity(
                type=CRMActivityType.CALL,
                subject="Discovery call",
                lead_id=lead.id,
                actor_id=owner.id,
            )
            session.add_all([note, activity])
            await session.commit()
            assert contact.company is company
            assert deal.originating_lead is lead
            assert stage.pipeline is pipeline

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "invalid",
    [
        CRMNote(body="No parent"),
        CRMNote(body="Two parents", company_id=None, contact_id=None),
        CRMActivity(type=CRMActivityType.CALL, subject="No parent"),
    ],
)
def test_crm_parent_constraints(invalid: object) -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            owner = user()
            session.add(owner)
            await session.flush()
            if isinstance(invalid, CRMNote):
                invalid.author_id = owner.id
            elif isinstance(invalid, CRMActivity):
                invalid.actor_id = owner.id
            session.add(invalid)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()
            owner = user()
            owner.email = "crm-contact-model@example.com"
            session.add(owner)
            await session.flush()
            session.add_all(
                [
                    Contact(
                        first_name="One",
                        last_name="Contact",
                        email="duplicate@example.com",
                        created_by=owner.id,
                    ),
                    Contact(
                        first_name="Two",
                        last_name="Contact",
                        email="duplicate@example.com",
                        created_by=owner.id,
                    ),
                ]
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    asyncio.run(exercise())


def test_duplicate_domain_and_email_constraints() -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            owner = user()
            session.add(owner)
            await session.flush()
            session.add_all(
                [
                    Company(
                        name="One",
                        domain="duplicate.example",
                        created_by=owner.id,
                    ),
                    Company(
                        name="Two",
                        domain="duplicate.example",
                        created_by=owner.id,
                    ),
                ]
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    asyncio.run(exercise())
