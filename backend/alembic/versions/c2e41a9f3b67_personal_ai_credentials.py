"""Personal encrypted model credentials."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c2e41a9f3b67"
down_revision = "0b3b93086d90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("key_suffix", sa.String(4), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_test_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("test_status", sa.String(16), nullable=True),
        sa.Column("test_code", sa.String(32), nullable=True),
        sa.Column("available_models", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.UniqueConstraint("owner_id", "provider", name="uq_ai_credentials_owner_provider"),
        sa.CheckConstraint("revision > 0 AND key_version > 0", name="ai_credential_versions"),
        sa.CheckConstraint("octet_length(ciphertext) > 28", name="ai_credential_ciphertext"),
        sa.CheckConstraint("test_status IS NULL OR test_status IN ('valid','invalid','unavailable')", name="ai_credential_test_status"),
    )


def downgrade():
    op.drop_table("ai_credentials")
