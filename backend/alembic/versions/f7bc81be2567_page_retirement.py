"""Keep removed page identities while excluding them from current content."""
from alembic import op
import sqlalchemy as sa

revision = "f7bc81be2567"
down_revision = "f6ab70ad1456"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("note_pages", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("uq_note_page_position", "note_pages", type_="unique")
    op.create_index("uq_note_page_active_position", "note_pages", ["note_id", "position"],
                    unique=True, postgresql_where=sa.text("deleted_at IS NULL"))


def downgrade():
    # Dropping the marker would silently resurrect removed content and could
    # collide with an active page's reused position. Require backup restoration
    # instead of deleting historical identities or changing user content.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM note_pages WHERE deleted_at IS NOT NULL)")):
        raise RuntimeError("page_retirement_downgrade_unsafe: retired pages exist; restore a compatible backup")
    op.drop_index("uq_note_page_active_position", table_name="note_pages")
    op.create_unique_constraint("uq_note_page_position", "note_pages", ["note_id", "position"])
    op.drop_column("note_pages", "deleted_at")
