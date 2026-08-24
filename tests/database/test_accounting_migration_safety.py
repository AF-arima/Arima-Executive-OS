from io import StringIO
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text

from tests.database.test_migrations import migration_config


def _upgrade_config(tmp_path: Path):
    database_path = tmp_path / "accounting-migration.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path}"
    return migration_config(async_url), create_engine(f"sqlite:///{database_path}")


def _seed_identity(connection):
    tenant_id = uuid4().hex
    user_id = uuid4().hex
    workspace_id = uuid4().hex
    timestamp = "2026-08-24 12:00:00+00:00"
    connection.execute(text("INSERT INTO tenants (id, name, created_at, updated_at) VALUES (:id, 'Tenant', :created, :updated)"), {"id": tenant_id, "created": timestamp, "updated": timestamp})
    connection.execute(text("INSERT INTO users (id, email, hashed_password, first_name, last_name, is_active, is_verified, failed_login_attempts, mfa_enabled, mfa_failed_attempts, created_at, updated_at) VALUES (:id, 'migration@example.com', 'hashed', 'Migration', 'User', 1, 1, 0, 0, 0, :created, :updated)"), {"id": user_id, "created": timestamp, "updated": timestamp})
    connection.execute(text("INSERT INTO workspaces (id, name, tenant_id, owner_id, created_at, updated_at) VALUES (:id, 'Workspace', :tenant, :user, :created, :updated)"), {"id": workspace_id, "tenant": tenant_id, "user": user_id, "created": timestamp, "updated": timestamp})
    return tenant_id, user_id, workspace_id


def test_accounting_upgrade_and_empty_downgrade_are_safe(tmp_path: Path) -> None:
    config, engine = _upgrade_config(tmp_path)
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM settled_trades")) == 0
        assert connection.scalar(text("SELECT COUNT(*) FROM customer_documents")) == 0
    command.downgrade(config, "20260824_0025")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'settled_trades'")) == 0
        assert connection.scalar(text("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'customer_documents'")) == 1
    engine.dispose()


def test_accounting_upgrade_accepts_valid_customer_and_clearing_data(tmp_path: Path) -> None:
    config, engine = _upgrade_config(tmp_path)
    command.upgrade(config, "head")
    with engine.begin() as connection:
        _, user_id, workspace_id = _seed_identity(connection)
        connection.execute(text("INSERT INTO financial_accounts (id, created_at, updated_at, workspace_id, user_id, asset, account_kind, status) VALUES (:id, :created, :updated, :workspace, :user, 'USD', 'customer', 'active')"), {"id": uuid4().hex, "created": "2026-08-24 12:00:00+00:00", "updated": "2026-08-24 12:00:00+00:00", "workspace": workspace_id, "user": user_id})
        connection.execute(text("INSERT INTO financial_accounts (id, created_at, updated_at, workspace_id, user_id, asset, account_kind, status) VALUES (:id, :created, :updated, :workspace, NULL, 'USD', 'clearing', 'active')"), {"id": uuid4().hex, "created": "2026-08-24 12:00:00+00:00", "updated": "2026-08-24 12:00:00+00:00", "workspace": workspace_id})
        inspector = inspect(connection)
        ledger_foreign_keys = inspector.get_foreign_keys("ledger_entries")
        assert any(
            "financial_accounts" in foreign_key["referred_table"]
            for foreign_key in ledger_foreign_keys
        )
    engine.dispose()


def test_postgresql_upgrade_does_not_recreate_financial_accounts() -> None:
    output_buffer = StringIO()
    config = Config("alembic.ini", output_buffer=output_buffer)
    config.attributes["database_url"] = (
        "postgresql+asyncpg://postgres:postgres@localhost/arima"
    )

    command.upgrade(config, "20260824_0025:20260824_0026", sql=True)

    sql = output_buffer.getvalue()
    assert "ALTER TABLE financial_accounts ALTER COLUMN user_id DROP NOT NULL" in sql
    assert "DROP CONSTRAINT pk_financial_accounts" not in sql
    assert "DROP CONSTRAINT fk_ledger_entries_financial_account_id_financial_accounts" not in sql


def test_downgrade_rejects_settled_trade_history_without_mutation(tmp_path: Path) -> None:
    config, engine = _upgrade_config(tmp_path)
    command.upgrade(config, "head")
    with engine.begin() as connection:
        _, user_id, workspace_id = _seed_identity(connection)
        trade_id = uuid4().hex
        connection.execute(text("INSERT INTO settled_trades (id, workspace_id, target_user_id, founder_actor_id, side, base_asset, quote_asset, quantity, price, quote_value, fee_asset, fee_amount, executed_at, status, idempotency_key, payload_fingerprint, reason, position_before_quantity, position_before_realized_pnl, position_after_quantity, position_after_realized_pnl, created_at, updated_at) VALUES (:id, :workspace, :user, :user, 'buy', 'BTC', 'USD', 1, 100, 100, 'USD', 0, :executed, 'recorded', 'migration-trade-key', 'fingerprint', 'test history', 0, 0, 1, 0, :created, :updated)"), {"id": trade_id, "workspace": workspace_id, "user": user_id, "executed": "2026-08-24 12:00:00+00:00", "created": "2026-08-24 12:00:00+00:00", "updated": "2026-08-24 12:00:00+00:00"})
    with pytest.raises(RuntimeError, match="immutable settled trade history"):
        command.downgrade(config, "20260824_0025")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM settled_trades")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM alembic_version")) == 1
    engine.dispose()


def test_downgrade_rejects_clearing_accounts_without_mutation(tmp_path: Path) -> None:
    config, engine = _upgrade_config(tmp_path)
    command.upgrade(config, "head")
    with engine.begin() as connection:
        _, user_id, workspace_id = _seed_identity(connection)
        connection.execute(text("INSERT INTO financial_accounts (id, created_at, updated_at, workspace_id, user_id, asset, account_kind, status) VALUES (:id, :created, :updated, :workspace, NULL, 'USD', 'clearing', 'active')"), {"id": uuid4().hex, "created": "2026-08-24 12:00:00+00:00", "updated": "2026-08-24 12:00:00+00:00", "workspace": workspace_id})
    with pytest.raises(RuntimeError, match="clearing accounts with NULL user_id"):
        command.downgrade(config, "20260824_0025")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM financial_accounts WHERE user_id IS NULL")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260824_0026"
    engine.dispose()
