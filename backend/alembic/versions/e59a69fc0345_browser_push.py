"""Session-scoped, nonportable browser push capabilities."""
from alembic import op
import sqlalchemy as sa

revision = "e59a69fc0345"
down_revision = "d48f58ebf234"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_sessions_user_id", "sessions", ["user_id", "id"])
    op.create_table("push_subscriptions",
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("endpoint_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(87), nullable=False),
        sa.Column("auth", sa.String(22), nullable=False),
        sa.Column("vapid_key_hash", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id", "session_id"], ["sessions.user_id", "sessions.id"], ondelete="CASCADE", name="fk_push_session_owner"))
    op.create_index("ix_push_subscriptions_owner_id", "push_subscriptions", ["owner_id"])
    op.create_index("ix_push_subscriptions_session_id", "push_subscriptions", ["session_id"])
    op.create_index("ix_push_subscriptions_owner_session", "push_subscriptions", ["owner_id", "session_id"])


def downgrade():
    op.drop_table("push_subscriptions")
    op.drop_constraint("uq_sessions_user_id", "sessions", type_="unique")
