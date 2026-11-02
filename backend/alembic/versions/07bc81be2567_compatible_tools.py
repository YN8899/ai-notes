"""Frozen provider identity, durable chat attempts, and private readonly tool history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "07bc81be2567"
down_revision = "f7bc81be2567"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("ai_runs", "model", type_=sa.String(256), existing_type=sa.String(64))
    op.add_column("ai_runs", sa.Column("provider", sa.String(32), nullable=False, server_default="deepseek"))
    op.add_column("ai_runs", sa.Column("provider_snapshot", postgresql.JSONB(), nullable=True))
    op.add_column("ai_runs", sa.Column("tools", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("ai_runs", sa.Column("tool_scope", postgresql.JSONB(), nullable=True))
    op.add_column("ai_runs", sa.Column("auth_session_id", sa.UUID(), nullable=True))
    op.drop_constraint("ai_step_state", "ai_steps", type_="check")
    op.create_check_constraint("ai_step_state", "ai_steps", "kind IN ('read','reduce','answer','chat') AND status IN ('running','succeeded','failed')")
    op.create_unique_constraint("uq_ai_step_run_id", "ai_steps", ["run_id", "id"])
    op.create_table("ai_tool_calls",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("provider_call_id", sa.String(128), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id", "run_id"], ["ai_runs.owner_id", "ai_runs.id"], name="fk_ai_tool_run_owner"),
        sa.ForeignKeyConstraint(["run_id", "step_id"], ["ai_steps.run_id", "ai_steps.id"], name="fk_ai_tool_step_run"),
        sa.UniqueConstraint("run_id", "position", name="uq_ai_tool_position"),
        sa.UniqueConstraint("run_id", "provider_call_id", name="uq_ai_tool_provider_id"),
        sa.CheckConstraint("position >= 0 AND position < 6", name="ai_tool_position"),
        sa.CheckConstraint("tool_name IN ('search_notes','read_note_excerpt','learning_summary')", name="ai_tool_name"),
        sa.CheckConstraint("status IN ('pending','succeeded','failed','interrupted')", name="ai_tool_state"),
        sa.CheckConstraint("result IS NULL OR octet_length(result) <= 8192", name="ai_tool_result_size"))
    op.create_index("ix_ai_tool_owner_run", "ai_tool_calls", ["owner_id", "run_id"])


def downgrade():
    # Data-preserving guard: old schemas cannot represent custom models/tools.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM ai_runs WHERE provider <> 'deepseek' OR length(model)>64 OR tools <> '[]'::jsonb")):
        raise RuntimeError("t32_downgrade_requires_export_and_explicit_data_resolution")
    if connection.scalar(sa.text("SELECT count(*) FROM ai_steps WHERE kind='chat'")):
        raise RuntimeError("t32_downgrade_has_durable_chat_attempts")
    op.drop_table("ai_tool_calls")
    op.drop_constraint("uq_ai_step_run_id", "ai_steps", type_="unique")
    op.drop_constraint("ai_step_state", "ai_steps", type_="check")
    op.create_check_constraint("ai_step_state", "ai_steps", "kind IN ('read','reduce','answer') AND status IN ('running','succeeded','failed')")
    for column in ("auth_session_id", "tool_scope", "tools", "provider_snapshot", "provider"):
        op.drop_column("ai_runs", column)
    op.alter_column("ai_runs", "model", type_=sa.String(64), existing_type=sa.String(256))
