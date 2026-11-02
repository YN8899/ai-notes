"""Durable notification owner cursor, no serial/identity sequence objects."""
from alembic import op
import sqlalchemy as sa

revision = "29de03d04789"
down_revision = "18cd92cf3678"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("notification_counters",
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("head_seq", sa.BigInteger(), nullable=False),
        sa.Column("read_revision", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("head_seq >= 0 AND read_revision >= 0", name="notification_counter_bounds"))
    op.add_column("notifications", sa.Column("seq", sa.BigInteger(), nullable=True))
    op.execute("""WITH ranked AS (
        SELECT id, row_number() OVER (PARTITION BY user_id ORDER BY created_at, id) AS seq FROM notifications
        ) UPDATE notifications n SET seq = r.seq FROM ranked r WHERE n.id = r.id""")
    op.execute("""INSERT INTO notification_counters(owner_id,head_seq,read_revision)
        SELECT user_id,max(seq),0 FROM notifications GROUP BY user_id""")
    op.alter_column("notifications", "seq", nullable=False)
    op.create_unique_constraint("uq_notification_owner_seq", "notifications", ["user_id", "seq"])
    op.create_check_constraint("notification_seq_positive", "notifications", "seq > 0")


def downgrade():
    # Removing cursor facts and later backfilling by created_at can reorder
    # already delivered cursors. A nonempty feed needs verified full-site
    # backup/restore, never an in-place destructive schema downgrade.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM notifications)")):
        raise RuntimeError("notification_feed_downgrade_has_data")
    op.drop_constraint("notification_seq_positive", "notifications", type_="check")
    op.drop_constraint("uq_notification_owner_seq", "notifications", type_="unique")
    op.drop_column("notifications", "seq")
    op.drop_table("notification_counters")
