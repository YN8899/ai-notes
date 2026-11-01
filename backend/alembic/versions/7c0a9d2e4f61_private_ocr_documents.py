"""Owner-scoped single-page OCR documents with independent correction revisions."""
from alembic import op
import sqlalchemy as sa

revision = "7c0a9d2e4f61"
down_revision = "2ef16e7ca892"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ocr_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("source_width", sa.Integer(), nullable=True),
        sa.Column("source_height", sa.Integer(), nullable=True),
        sa.Column("corrected_text", sa.Text(), nullable=True),
        sa.Column("reviewed", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["owner_id", "note_id"], ["notes.owner_id", "notes.id"], name="fk_ocr_note_owner"),
        sa.ForeignKeyConstraint(["owner_id", "file_id"], ["files.owner_id", "files.id"], name="fk_ocr_file_owner"),
        sa.ForeignKeyConstraint(["owner_id", "run_id"], ["ai_runs.owner_id", "ai_runs.id"], name="fk_ocr_run_owner"),
        sa.UniqueConstraint("owner_id", "request_key", name="uq_ocr_request"),
        sa.UniqueConstraint("run_id", name="uq_ocr_run"),
        sa.CheckConstraint("revision > 0", name="ocr_revision"),
        sa.CheckConstraint("corrected_text IS NULL OR length(corrected_text) <= 64000", name="ocr_text_length"),
        sa.CheckConstraint("NOT reviewed OR corrected_text IS NOT NULL", name="ocr_reviewed_text"),
        sa.CheckConstraint("(source_width IS NULL OR source_width > 0) AND (source_height IS NULL OR source_height > 0)", name="ocr_source_dimensions"),
    )
    op.create_index("ix_ocr_owner_page", "ocr_documents", ["owner_id", "note_id", "page_id", "created_at"])


def downgrade():
    op.drop_table("ocr_documents")
