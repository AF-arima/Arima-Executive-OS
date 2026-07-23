from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

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
    engine.dispose()

    command.check(config)
    command.downgrade(config, "base")

    engine = create_engine(sync_url)
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()
