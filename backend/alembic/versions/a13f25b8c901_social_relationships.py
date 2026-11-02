"""Freeze member privacy, canonical relationships, follows and read-only grants."""
from alembic import op
import sqlalchemy as sa

revision = "a13f25b8c901"
down_revision = "84c6e9b302df"
branch_labels = None
depends_on = None


def identity():
    return [sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    op.create_table("social_profiles",
        *identity(),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("bio", sa.String(240), nullable=False),
        sa.Column("subject", sa.String(80), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("visibility IN ('friends','private')", name="social_profile_visibility"),
        sa.CheckConstraint("revision > 0", name="social_profile_revision"))
    op.create_table("friendships",
        *identity(),
        sa.Column("user_low_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("user_high_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requester_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_low_id", "user_high_id", name="uq_friendship_pair"),
        sa.CheckConstraint("user_low_id < user_high_id", name="friendship_canonical_pair"),
        sa.CheckConstraint("requester_id IN (user_low_id,user_high_id)", name="friendship_requester"),
        sa.CheckConstraint("status IN ('pending','accepted','rejected','removed')", name="friendship_status"),
        sa.CheckConstraint("revision > 0", name="friendship_revision"))
    op.create_index("ix_friendships_low_status", "friendships", ["user_low_id", "status"])
    op.create_index("ix_friendships_high_status", "friendships", ["user_high_id", "status"])
    op.create_table("follows",
        *identity(),
        sa.Column("follower_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("followee_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("follower_id", "followee_id", name="uq_follow_pair"),
        sa.CheckConstraint("follower_id <> followee_id", name="follow_not_self"))
    op.create_index("ix_follows_follower_id", "follows", ["follower_id"])
    op.create_index("ix_follows_followee_id", "follows", ["followee_id"])
    op.create_table("note_grants",
        *identity(),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("grantee_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id", "note_id"], ["notes.owner_id", "notes.id"], name="fk_note_grant_owner"),
        sa.UniqueConstraint("note_id", "grantee_id", name="uq_note_grant_recipient"),
        sa.CheckConstraint("owner_id <> grantee_id", name="note_grant_not_self"),
        sa.CheckConstraint("revision > 0", name="note_grant_revision"))
    op.create_index("ix_note_grants_owner_id", "note_grants", ["owner_id"])
    op.create_index("ix_note_grants_grantee_id", "note_grants", ["grantee_id"])


def downgrade():
    for table in ("note_grants", "follows", "friendships", "social_profiles"):
        op.drop_table(table)
