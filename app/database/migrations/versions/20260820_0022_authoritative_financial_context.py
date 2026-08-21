"""Add explicit tenant linkage and withdrawal request fingerprints."""

from alembic import op
import sqlalchemy as sa


revision = "20260820_0022"
down_revision = "20260820_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    tenant_column = sa.Column("tenant_id", sa.Uuid(), nullable=True)
    if connection.dialect.name == "postgresql":
        op.add_column("workspaces", tenant_column)
        op.create_foreign_key(
            "fk_workspaces_tenant_id",
            "workspaces",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    else:
        with op.batch_alter_table("workspaces", recreate="always") as batch_op:
            batch_op.add_column(tenant_column)
            batch_op.create_foreign_key("fk_workspaces_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
    workspaces = sa.table(
        "workspaces",
        sa.column("id", sa.Uuid()), sa.column("name", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)), sa.column("tenant_id", sa.Uuid()),
    )
    tenants = sa.table(
        "tenants",
        sa.column("id", sa.Uuid()), sa.column("name", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for row in connection.execute(sa.select(workspaces.c.id, workspaces.c.name, workspaces.c.created_at, workspaces.c.updated_at)).mappings():
        connection.execute(tenants.insert().values(id=row["id"], name=f"{row['name']} tenant", created_at=row["created_at"], updated_at=row["updated_at"]))
        connection.execute(workspaces.update().where(workspaces.c.id == row["id"]).values(tenant_id=row["id"]))
    op.create_index("ix_workspaces_tenant_id", "workspaces", ["tenant_id"])
    op.add_column("withdrawal_requests", sa.Column("request_fingerprint", sa.String(128), nullable=True))
    op.create_index("ix_withdrawal_requests_request_fingerprint", "withdrawal_requests", ["request_fingerprint"])
    withdrawals = sa.table("withdrawal_requests", sa.column("id", sa.Uuid()), sa.column("request_fingerprint", sa.String()))
    for row in connection.execute(sa.select(withdrawals.c.id)).mappings():
        connection.execute(withdrawals.update().where(withdrawals.c.id == row["id"]).values(request_fingerprint=f"legacy-unverified:{row['id']}"))
    with op.batch_alter_table("withdrawal_requests", recreate="always") as batch_op:
        batch_op.alter_column("request_fingerprint", nullable=False)
    op.add_column("trade_executions", sa.Column("request_fingerprint", sa.String(128), nullable=True))
    op.create_index("ix_trade_executions_request_fingerprint", "trade_executions", ["request_fingerprint"])
    executions = sa.table("trade_executions", sa.column("id", sa.Uuid()), sa.column("request_fingerprint", sa.String()))
    for row in connection.execute(sa.select(executions.c.id)).mappings():
        connection.execute(executions.update().where(executions.c.id == row["id"]).values(request_fingerprint=f"legacy-unverified:{row['id']}"))
    with op.batch_alter_table("trade_executions", recreate="always") as batch_op:
        batch_op.alter_column("request_fingerprint", nullable=False)


def downgrade() -> None:
    op.drop_index("ix_trade_executions_request_fingerprint", table_name="trade_executions")
    op.drop_column("trade_executions", "request_fingerprint")
    op.drop_index("ix_withdrawal_requests_request_fingerprint", table_name="withdrawal_requests")
    op.drop_column("withdrawal_requests", "request_fingerprint")
    op.drop_index("ix_workspaces_tenant_id", table_name="workspaces")
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.drop_constraint("fk_workspaces_tenant_id", "workspaces", type_="foreignkey")
        op.drop_column("workspaces", "tenant_id")
    else:
        with op.batch_alter_table("workspaces", recreate="always") as batch_op:
            batch_op.drop_column("tenant_id")
    op.drop_table("tenants")
