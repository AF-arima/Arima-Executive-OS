"""Add tenant/workspace-scoped QLab and research records."""

from alembic import op
import sqlalchemy as sa


revision = "20260820_0023"
down_revision = "20260820_0022"
branch_labels = None
depends_on = None


def _common_columns():
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
    ]


def upgrade() -> None:
    for table, extra, indexes in (
        (
            "qlab_experiments",
            [
                sa.Column("name", sa.String(160), nullable=False),
                sa.Column("description", sa.Text(), nullable=True),
                sa.Column("status", sa.String(32), nullable=False),
                sa.Column("provenance", sa.JSON(), nullable=False),
            ],
            [
                ("ix_qlab_experiments_tenant_id", "tenant_id"),
                ("ix_qlab_experiments_workspace_id", "workspace_id"),
                ("ix_qlab_experiments_account_id", "account_id"),
                ("ix_qlab_experiments_created_by_id", "created_by_id"),
                ("ix_qlab_experiments_workspace_created", "workspace_id", "created_at"),
            ],
        ),
        (
            "qlab_datasets",
            [
                sa.Column("experiment_id", sa.Uuid(), nullable=False),
                sa.Column("name", sa.String(160), nullable=False),
                sa.Column("source", sa.String(240), nullable=False),
                sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("status", sa.String(32), nullable=False),
                sa.Column("provenance", sa.JSON(), nullable=False),
                sa.Column("metadata_json", sa.JSON(), nullable=False),
            ],
            [
                ("ix_qlab_datasets_tenant_id", "tenant_id"),
                ("ix_qlab_datasets_workspace_id", "workspace_id"),
                ("ix_qlab_datasets_account_id", "account_id"),
                ("ix_qlab_datasets_experiment_id", "experiment_id"),
                ("ix_qlab_datasets_workspace_created", "workspace_id", "created_at"),
            ],
        ),
        (
            "qlab_models",
            [
                sa.Column("experiment_id", sa.Uuid(), nullable=False),
                sa.Column("name", sa.String(160), nullable=False),
                sa.Column("version", sa.String(80), nullable=False),
                sa.Column("status", sa.String(32), nullable=False),
                sa.Column("provenance", sa.JSON(), nullable=False),
            ],
            [
                ("ix_qlab_models_tenant_id", "tenant_id"),
                ("ix_qlab_models_workspace_id", "workspace_id"),
                ("ix_qlab_models_account_id", "account_id"),
                ("ix_qlab_models_experiment_id", "experiment_id"),
                ("ix_qlab_models_workspace_created", "workspace_id", "created_at"),
            ],
        ),
        (
            "qlab_runs",
            [
                sa.Column("experiment_id", sa.Uuid(), nullable=False),
                sa.Column("dataset_id", sa.Uuid(), nullable=True),
                sa.Column("model_id", sa.Uuid(), nullable=True),
                sa.Column("status", sa.String(32), nullable=False),
                sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
                sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
                sa.Column("result", sa.JSON(), nullable=False),
                sa.Column("provenance", sa.JSON(), nullable=False),
                sa.Column("failure_reason", sa.String(240), nullable=True),
            ],
            [
                ("ix_qlab_runs_tenant_id", "tenant_id"),
                ("ix_qlab_runs_workspace_id", "workspace_id"),
                ("ix_qlab_runs_account_id", "account_id"),
                ("ix_qlab_runs_experiment_id", "experiment_id"),
                ("ix_qlab_runs_workspace_created", "workspace_id", "created_at"),
            ],
        ),
        (
            "research_records",
            [
                sa.Column("title", sa.String(200), nullable=False),
                sa.Column("content", sa.Text(), nullable=False),
                sa.Column("source", sa.String(240), nullable=False),
                sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("status", sa.String(32), nullable=False),
                sa.Column("provenance", sa.JSON(), nullable=False),
                sa.Column("tags", sa.JSON(), nullable=False),
            ],
            [
                ("ix_research_records_tenant_id", "tenant_id"),
                ("ix_research_records_workspace_id", "workspace_id"),
                ("ix_research_records_account_id", "account_id"),
                ("ix_research_records_created_by_id", "created_by_id"),
                ("ix_research_records_workspace_created", "workspace_id", "created_at"),
            ],
        ),
    ):
        foreign_keys = [
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(
                ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["account_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(
                ["created_by_id"], ["users.id"], ondelete="RESTRICT"
            ),
        ]
        if table == "qlab_datasets":
            foreign_keys.append(
                sa.ForeignKeyConstraint(
                    ["experiment_id"], ["qlab_experiments.id"], ondelete="CASCADE"
                )
            )
        elif table == "qlab_models":
            foreign_keys.append(
                sa.ForeignKeyConstraint(
                    ["experiment_id"], ["qlab_experiments.id"], ondelete="CASCADE"
                )
            )
        elif table == "qlab_runs":
            foreign_keys.extend(
                [
                    sa.ForeignKeyConstraint(
                        ["experiment_id"], ["qlab_experiments.id"], ondelete="CASCADE"
                    ),
                    sa.ForeignKeyConstraint(
                        ["dataset_id"], ["qlab_datasets.id"], ondelete="SET NULL"
                    ),
                    sa.ForeignKeyConstraint(
                        ["model_id"], ["qlab_models.id"], ondelete="SET NULL"
                    ),
                ]
            )
        op.create_table(
            table,
            *(_common_columns() + extra),
            *foreign_keys,
            sa.PrimaryKeyConstraint("id"),
        )
        for name, *columns in indexes:
            op.create_index(name, table, columns)


def downgrade() -> None:
    for table in (
        "research_records",
        "qlab_runs",
        "qlab_models",
        "qlab_datasets",
        "qlab_experiments",
    ):
        op.drop_table(table)
