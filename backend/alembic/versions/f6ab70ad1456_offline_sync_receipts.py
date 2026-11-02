"""Permanent offline success receipts, committed with their business result."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f6ab70ad1456"
down_revision = "e59a69fc0345"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("offline_sync_states",
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("epoch", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("offline_sync_receipts",
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operation_id", sa.UUID(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=False),
        sa.Column("resource_mapping", postgresql.JSONB(), nullable=False),
        sa.Column("applied_revision", sa.Integer(), nullable=True),
        sa.Column("transport", postgresql.JSONB(), nullable=False),
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "operation_id", name="uq_offline_receipt_operation"),
        sa.CheckConstraint("applied_revision IS NULL OR applied_revision > 0", name="offline_receipt_revision"))
    op.create_index("ix_offline_sync_receipts_owner_id", "offline_sync_receipts", ["owner_id"])


def downgrade():
    op.drop_table("offline_sync_receipts")
    op.drop_table("offline_sync_states")
