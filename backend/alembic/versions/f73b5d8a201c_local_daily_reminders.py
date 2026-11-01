"""Freeze timezone preferences and current-local-day reminder ledger."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f73b5d8a201c"
down_revision = "e62a9c4f710b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("user_learning_settings",
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("review_time", sa.String(5), nullable=False),
        sa.Column("reminder_enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="learning_settings_revision"),
        sa.CheckConstraint("review_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'", name="learning_settings_time"))
    op.create_index("ix_learning_settings_check", "user_learning_settings", ["last_checked_at", "owner_id"])
    op.create_table("daily_review_digests",
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("counts", postgresql.JSONB(), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id"), unique=True, nullable=True),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "kind", "local_date", name="uq_daily_review_digest"),
        sa.CheckConstraint("kind = 'daily_review' AND jsonb_typeof(counts) = 'object'", name="daily_review_digest_shape"))
    op.create_index("ix_daily_review_digest_owner", "daily_review_digests", ["owner_id", "local_date"])


def downgrade():
    op.drop_table("daily_review_digests")
    op.drop_table("user_learning_settings")
