from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.database.models import Base


def migration_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    return config


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
    } == {"ix_users_email"}
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("tasks")
    } == {"ck_tasks_task_priority", "ck_tasks_task_status"}
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("audit_logs")
    } == {"ck_audit_logs_audit_action", "ck_audit_logs_audit_entity"}
    assert {
        index["name"]
        for index in inspector.get_indexes("audit_logs")
    } == {
        "ix_audit_logs_actor_id",
        "ix_audit_logs_entity_id",
        "ix_audit_logs_timestamp",
    }
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
