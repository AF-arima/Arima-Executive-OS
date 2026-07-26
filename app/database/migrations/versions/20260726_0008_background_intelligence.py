"""Add background intelligence persistence.

Revision ID: 20260726_0008
Revises: 20260726_0007
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260726_0008"
down_revision: str | None = "20260726_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_STATES = (
    "pending",
    "scheduled",
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "paused",
    "blocked",
    "waiting_for_approval",
    "expired",
)


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
    )


def _identity() -> sa.Column:
    return sa.Column("id", sa.Uuid(), nullable=False)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "background_job_definitions",
        sa.Column("job_name", sa.String(length=150), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("required_permissions", sa.JSON(), nullable=False),
        sa.Column("approval_policy", sa.String(length=50), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        _identity(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_background_job_definitions"),
        sa.UniqueConstraint(
            "job_name",
            "version",
            name="uq_background_job_definitions_name_version",
        ),
    )
    op.create_index(
        "ix_background_job_definitions_job_name",
        "background_job_definitions",
        ["job_name"],
    )

    op.create_table(
        "background_job_schedules",
        sa.Column("job_definition_id", sa.Uuid(), nullable=False),
        sa.Column("job_name", sa.String(length=150), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "schedule_type",
            _enum(
                "background_schedule_type",
                "one_time",
                "recurring",
                "interval",
                "calendar",
                "conditional",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("background_job_state_schedule", *JOB_STATES),
            nullable=False,
        ),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("maximum_runs", sa.Integer(), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        _identity(),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["job_definition_id"],
            ["background_job_definitions.id"],
            name=(
                "fk_background_job_schedules_job_definition_id_"
                "background_job_definitions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_background_job_schedules_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_definitions.id"],
            name=(
                "fk_background_job_schedules_agent_id_agent_definitions"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            name=(
                "fk_background_job_schedules_conversation_id_"
                "agent_conversations"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_background_job_schedules_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_background_job_schedules"),
    )
    for column in (
        "status",
        "next_run_at",
        "job_name",
        "agent_id",
        "user_id",
    ):
        op.create_index(
            f"ix_background_job_schedules_{column}",
            "background_job_schedules",
            [column],
        )

    op.create_table(
        "background_job_executions",
        sa.Column("job_definition_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=True),
        sa.Column("job_name", sa.String(length=150), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "trigger_source",
            _enum(
                "background_trigger_source",
                "schedule",
                "manual",
                "condition",
                "event",
                "system",
                "retry",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("background_job_state_execution", *JOB_STATES),
            nullable=False,
        ),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        _identity(),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["job_definition_id"],
            ["background_job_definitions.id"],
            name=(
                "fk_background_job_executions_job_definition_id_"
                "background_job_definitions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["background_job_schedules.id"],
            name=(
                "fk_background_job_executions_schedule_id_"
                "background_job_schedules"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_background_job_executions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_definitions.id"],
            name=(
                "fk_background_job_executions_agent_id_agent_definitions"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_background_job_executions_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_background_job_executions"),
    )
    for column in (
        "status",
        "job_name",
        "agent_id",
        "user_id",
        "correlation_id",
    ):
        op.create_index(
            f"ix_background_job_executions_{column}",
            "background_job_executions",
            [column],
        )

    op.create_table(
        "background_job_attempts",
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            _enum("background_job_state_attempt", *JOB_STATES),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        _identity(),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["background_job_executions.id"],
            name=(
                "fk_background_job_attempts_execution_id_"
                "background_job_executions"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_background_job_attempts"),
        sa.UniqueConstraint(
            "execution_id",
            "attempt_number",
            name="uq_background_job_attempts_execution_attempt",
        ),
    )
    op.create_index(
        "ix_background_job_attempts_status",
        "background_job_attempts",
        ["status"],
    )

    op.create_table(
        "background_job_events",
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        sa.Column("schedule_id", sa.Uuid(), nullable=True),
        sa.Column("job_name", sa.String(length=150), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "from_state",
            _enum("background_job_state_event_from", *JOB_STATES),
            nullable=True,
        ),
        sa.Column(
            "to_state",
            _enum("background_job_state_event_to", *JOB_STATES),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("event_metadata", sa.JSON(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        _identity(),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["background_job_executions.id"],
            name=(
                "fk_background_job_events_execution_id_"
                "background_job_executions"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["background_job_schedules.id"],
            name=(
                "fk_background_job_events_schedule_id_"
                "background_job_schedules"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_background_job_events"),
    )
    for column in ("job_name", "correlation_id", "to_state"):
        op.create_index(
            f"ix_background_job_events_{column}",
            "background_job_events",
            [column],
        )


def downgrade() -> None:
    for column in ("to_state", "correlation_id", "job_name"):
        op.drop_index(
            f"ix_background_job_events_{column}",
            table_name="background_job_events",
        )
    op.drop_table("background_job_events")
    op.drop_index(
        "ix_background_job_attempts_status",
        table_name="background_job_attempts",
    )
    op.drop_table("background_job_attempts")
    for column in (
        "correlation_id",
        "user_id",
        "agent_id",
        "job_name",
        "status",
    ):
        op.drop_index(
            f"ix_background_job_executions_{column}",
            table_name="background_job_executions",
        )
    op.drop_table("background_job_executions")
    for column in (
        "user_id",
        "agent_id",
        "job_name",
        "next_run_at",
        "status",
    ):
        op.drop_index(
            f"ix_background_job_schedules_{column}",
            table_name="background_job_schedules",
        )
    op.drop_table("background_job_schedules")
    op.drop_index(
        "ix_background_job_definitions_job_name",
        table_name="background_job_definitions",
    )
    op.drop_table("background_job_definitions")
