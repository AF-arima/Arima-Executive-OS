from io import StringIO
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from app.database.models import Base


def migration_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    return config


def test_background_intelligence_migration_compiles_for_postgresql() -> None:
    output_buffer = StringIO()
    config = Config("alembic.ini", output_buffer=output_buffer)
    config.attributes["database_url"] = (
        "postgresql+asyncpg://postgres:postgres@localhost/arima"
    )

    command.upgrade(config, "20260726_0008", sql=True)

    assert "CREATE TABLE background_job_attempts" in output_buffer.getvalue()


def test_models_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()

    for table in Base.metadata.sorted_tables:
        str(CreateTable(table).compile(dialect=dialect))


def test_initial_migration_matches_metadata_and_downgrades(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "migration.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path}"
    sync_url = f"sqlite:///{database_path}"
    config = migration_config(async_url)

    command.upgrade(config, "head")

    engine = create_engine(sync_url)
    inspector = inspect(engine)
    expected_tables = set(Base.metadata.tables)

    assert set(inspector.get_table_names()) == expected_tables | {
        "alembic_version"
    }
    assert {
        "security_tokens",
        "security_events",
        "rate_limit_buckets",
        "refresh_token_sessions",
        "workspaces",
        "workspace_memberships",
        "market_prices",
        "data_feed_observations",
        "voice_sessions",
    }.issubset(expected_tables)
    for table_name in expected_tables:
        migrated_columns = {
            column["name"]
            for column in inspector.get_columns(table_name)
        }
        model_columns = {
            column.name
            for column in Base.metadata.tables[table_name].columns
        }
        assert migrated_columns == model_columns

    assert inspector.get_pk_constraint("users")["name"] == "pk_users"
    assert inspector.get_pk_constraint("user_roles")["name"] == (
        "pk_user_roles"
    )
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("roles")
    } == {"uq_roles_name"}
    assert {
        index["name"] for index in inspector.get_indexes("users")
    } == {"ix_users_email", "ix_users_locked_until"}
    assert {
        index["name"]
        for index in inspector.get_indexes("security_tokens")
    } == {
        "ix_security_tokens_expires_at",
        "ix_security_tokens_purpose",
        "ix_security_tokens_token_hash",
        "ix_security_tokens_user_id",
    }
    security_token_checks = {
        constraint["name"]: str(constraint["sqltext"])
        for constraint in inspector.get_check_constraints("security_tokens")
    }
    assert set(security_token_checks) == {
        "ck_security_tokens_security_token_purpose"
    }
    assert all(
        value in security_token_checks["ck_security_tokens_security_token_purpose"]
        for value in (
            "email_verification",
            "password_reset",
            "email_change",
        )
    )
    assert {
        index["name"]
        for index in inspector.get_indexes("security_events")
    } == {
        "ix_security_events_event_type",
        "ix_security_events_occurred_at",
        "ix_security_events_user_id",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("rate_limit_buckets")
    } == {"uq_rate_limit_buckets_scope_key_window"}
    assert {
        index["name"]
        for index in inspector.get_indexes("rate_limit_buckets")
    } == {"ix_rate_limit_buckets_scope_key_window"}
    assert inspector.get_pk_constraint("rate_limit_buckets")["name"] == (
        "pk_rate_limit_buckets"
    )
    assert {
        "ix_projects_archived_at",
        "ix_projects_created_at",
        "ix_projects_status",
    }.issubset(
        {
            index["name"]
            for index in inspector.get_indexes("projects")
        }
    )
    assert {
        "ix_tasks_created_at",
        "ix_tasks_due_date",
        "ix_tasks_status",
    }.issubset(
        {
            index["name"] for index in inspector.get_indexes("tasks")
        }
    )
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("tasks")
    } == {"ck_tasks_task_priority", "ck_tasks_task_status"}
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("audit_logs")
    } == {"ck_audit_logs_audit_action", "ck_audit_logs_audit_entity"}
    audit_entity_checks = {
        constraint["name"]: str(constraint["sqltext"])
        for constraint in inspector.get_check_constraints("audit_logs")
    }
    assert "data_feed_observation" in audit_entity_checks[
        "ck_audit_logs_audit_entity"
    ]
    assert {
        index["name"]
        for index in inspector.get_indexes("data_feed_observations")
    } == {
        "ix_data_feed_observations_correlation_id",
        "ix_data_feed_observations_entered_by_id",
        "ix_data_feed_observations_entered_created",
        "ix_data_feed_observations_feed_observed",
    }
    assert {
        index["name"]
        for index in inspector.get_indexes("audit_logs")
    } == {
        "ix_audit_logs_actor_id",
        "ix_audit_logs_actor_timestamp",
        "ix_audit_logs_entity_action_timestamp",
        "ix_audit_logs_entity_id",
        "ix_audit_logs_project_timestamp",
        "ix_audit_logs_timestamp",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("notifications")
    } == {"ck_notifications_notification_type"}
    assert {
        index["name"]
        for index in inspector.get_indexes("notifications")
    } == {
        "ix_notifications_expires_at",
        "ix_notifications_user_created",
        "ix_notifications_user_read_created",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("notifications")
    } == {"uq_notifications_dedupe_key"}
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("crm_notes")
    } == {"ck_crm_notes_exactly_one_parent"}
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("crm_activities")
    } == {
        "ck_crm_activities_crm_activity_type",
        "ck_crm_activities_has_parent",
    }
    assert {
        "ix_crm_companies_owner_status",
        "ix_crm_companies_archived_at",
        "uq_crm_companies_domain",
    }.issubset(
        {
            index["name"]
            for index in inspector.get_indexes("crm_companies")
        }
    )
    assert {
        "ix_crm_deals_pipeline_stage",
        "ix_crm_deals_owner_status",
        "ix_crm_deals_expected_close_date",
    }.issubset(
        {
            index["name"] for index in inspector.get_indexes("crm_deals")
        }
    )
    engine.dispose()

    command.check(config)
    command.downgrade(config, "base")

    engine = create_engine(sync_url)
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()


def test_management_migration_backfills_existing_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "backfill.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path}"
    sync_url = f"sqlite:///{database_path}"
    config = migration_config(async_url)
    command.upgrade(config, "20260723_0002")

    user_id = uuid4().hex
    project_id = uuid4().hex
    task_id = uuid4().hex
    timestamp = "2026-07-23 12:00:00+00:00"
    engine = create_engine(sync_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(email, hashed_password, first_name, last_name, "
                "is_active, is_verified, id, created_at, updated_at) "
                "VALUES (:email, :password, :first_name, :last_name, "
                "1, 0, :id, :created_at, :updated_at)"
            ),
            {
                "email": "existing@example.com",
                "password": "hashed",
                "first_name": "Existing",
                "last_name": "User",
                "id": user_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO projects "
                "(name, status, owner_id, id, created_at, updated_at) "
                "VALUES ('Existing project', 'active', :owner_id, "
                ":id, :created_at, :updated_at)"
            ),
            {
                "owner_id": user_id,
                "id": project_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO tasks "
                "(title, status, priority, project_id, id, "
                "created_at, updated_at) "
                "VALUES ('Existing task', 'todo', 'medium', "
                ":project_id, :id, :created_at, :updated_at)"
            ),
            {
                "project_id": project_id,
                "id": task_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(sync_url)
    with engine.connect() as connection:
        project_creator = connection.scalar(
            text(
                "SELECT created_by FROM projects WHERE id = :project_id"
            ),
            {"project_id": project_id},
        )
        task_creator = connection.scalar(
            text("SELECT created_by FROM tasks WHERE id = :task_id"),
            {"task_id": task_id},
        )
    assert project_creator == user_id
    assert task_creator == user_id
    engine.dispose()

    command.downgrade(config, "20260723_0002")
    engine = create_engine(sync_url)
    inspector = inspect(engine)
    assert "created_by" not in {
        column["name"] for column in inspector.get_columns("projects")
    }
    assert "created_by" not in {
        column["name"] for column in inspector.get_columns("tasks")
    }
    engine.dispose()


def test_dashboard_migration_backfills_audit_project_scope(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "dashboard-backfill.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path}"
    sync_url = f"sqlite:///{database_path}"
    config = migration_config(async_url)
    command.upgrade(config, "20260723_0003")

    user_id = uuid4().hex
    project_id = uuid4().hex
    task_id = uuid4().hex
    project_audit_id = uuid4().hex
    task_audit_id = uuid4().hex
    timestamp = "2026-07-23 12:00:00+00:00"
    engine = create_engine(sync_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(email, hashed_password, first_name, last_name, "
                "is_active, is_verified, id, created_at, updated_at) "
                "VALUES ('audit@example.com', 'hashed', 'Audit', "
                "'User', 1, 0, :id, :created_at, :updated_at)"
            ),
            {
                "id": user_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO projects "
                "(name, status, owner_id, created_by, id, "
                "created_at, updated_at) VALUES "
                "('Audit project', 'active', :user_id, :user_id, "
                ":id, :created_at, :updated_at)"
            ),
            {
                "user_id": user_id,
                "id": project_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
        connection.execute(
            text(
                "INSERT INTO tasks "
                "(title, status, priority, project_id, created_by, id, "
                "created_at, updated_at) VALUES "
                "('Audit task', 'todo', 'medium', :project_id, "
                ":user_id, :id, :created_at, :updated_at)"
            ),
            {
                "project_id": project_id,
                "user_id": user_id,
                "id": task_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
        for audit_id, entity, entity_id in (
            (project_audit_id, "project", project_id),
            (task_audit_id, "task", task_id),
        ):
            connection.execute(
                text(
                    "INSERT INTO audit_logs "
                    "(actor_id, action, entity, entity_id, timestamp, id) "
                    "VALUES (:actor_id, 'create', :entity, :entity_id, "
                    ":timestamp, :id)"
                ),
                {
                    "actor_id": user_id,
                    "entity": entity,
                    "entity_id": entity_id,
                    "timestamp": timestamp,
                    "id": audit_id,
                },
            )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(sync_url)
    with engine.connect() as connection:
        project_ids = connection.execute(
            text(
                "SELECT project_id FROM audit_logs "
                "ORDER BY entity, id"
            )
        ).scalars()
        assert set(project_ids) == {project_id}
    inspector = inspect(engine)
    notification_fks = inspector.get_foreign_keys("notifications")
    assert notification_fks[0]["referred_table"] == "users"
    assert notification_fks[0]["options"]["ondelete"] == "CASCADE"
    engine.dispose()

    command.downgrade(config, "20260723_0003")
    engine = create_engine(sync_url)
    inspector = inspect(engine)
    assert "notifications" not in inspector.get_table_names()
    assert "project_id" not in {
        column["name"]
        for column in inspector.get_columns("audit_logs")
    }
    engine.dispose()


def test_case_insensitive_email_migration_normalizes_legacy_accounts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "email-normalization.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path}"
    sync_url = f"sqlite:///{database_path}"
    config = migration_config(async_url)
    command.upgrade(config, "20260728_0010")

    user_id = uuid4().hex
    timestamp = "2026-07-28 12:00:00+00:00"
    engine = create_engine(sync_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(email, hashed_password, first_name, last_name, "
                "is_active, is_verified, failed_login_attempts, id, "
                "created_at, updated_at) "
                "VALUES ('Legacy.User@Example.COM', 'hashed', 'Legacy', "
                "'User', 1, 1, 0, :id, :created_at, :updated_at)"
            ),
            {
                "id": user_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(sync_url)
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT email FROM users WHERE id = :id"),
            {"id": user_id},
        ) == "legacy.user@example.com"
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(email, hashed_password, first_name, last_name, "
                    "is_active, is_verified, failed_login_attempts, id, "
                    "created_at, updated_at) "
                    "VALUES ('LEGACY.USER@example.com', 'hashed', 'Other', "
                    "'User', 1, 1, 0, :id, :created_at, :updated_at)"
                ),
                {
                    "id": uuid4().hex,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )
    engine.dispose()


def test_identity_migration_bounds_legacy_workspace_names(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "identity-workspace-name.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path}"
    sync_url = f"sqlite:///{database_path}"
    config = migration_config(async_url)
    command.upgrade(config, "20260726_0008")

    user_id = uuid4().hex
    first_name = "A" * 100
    last_name = "B" * 100
    timestamp = "2026-07-28 12:00:00+00:00"
    engine = create_engine(sync_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(email, hashed_password, first_name, last_name, "
                "is_active, is_verified, id, created_at, updated_at) "
                "VALUES (:email, 'hashed', :first_name, :last_name, "
                "1, 1, :id, :created_at, :updated_at)"
            ),
            {
                "email": "long-name@example.com",
                "first_name": first_name,
                "last_name": last_name,
                "id": user_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )
    engine.dispose()

    command.upgrade(config, "20260727_0009")
    engine = create_engine(sync_url)
    with engine.connect() as connection:
        workspace_name = connection.scalar(
            text("SELECT name FROM workspaces WHERE owner_id = :owner_id"),
            {"owner_id": user_id},
        )
    assert workspace_name is not None
    assert workspace_name.endswith(" Workspace")
    assert len(workspace_name) == 160
    engine.dispose()
