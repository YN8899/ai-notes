"""Frozen community drafts, independent snapshots, interaction and moderation."""
from alembic import op

revision = "c37f47dae123"
down_revision = "b26f36c9d012"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE community_posts (
 id uuid PRIMARY KEY, created_at timestamptz NOT NULL,
 owner_id uuid NOT NULL REFERENCES users(id), title varchar(200) NOT NULL,
 body_json jsonb NOT NULL, plain_text text NOT NULL, subject varchar(80) NOT NULL,
 post_type varchar(16) NOT NULL, tags jsonb NOT NULL,
 source_note_id uuid REFERENCES notes(id) ON DELETE SET NULL, source_revision integer,
 revision integer NOT NULL, status varchar(16) NOT NULL,
 live_snapshot_id uuid, preparing_snapshot_id uuid, published_at timestamptz,
 updated_at timestamptz NOT NULL, moderation_reason varchar(500),
 CONSTRAINT uq_community_post_owner UNIQUE(owner_id,id),
 CONSTRAINT community_post_revision CHECK(revision > 0),
 CONSTRAINT community_post_status CHECK(status IN ('draft','published','withdrawn','hidden')),
 CONSTRAINT community_post_type CHECK(post_type IN ('note','question','experience','resource')),
 CONSTRAINT community_post_source_revision CHECK(source_revision IS NULL OR source_revision > 0)
);
CREATE INDEX ix_community_posts_owner_id ON community_posts(owner_id);
CREATE INDEX ix_community_posts_published_at ON community_posts(published_at);
CREATE INDEX ix_community_posts_feed ON community_posts(status,published_at,id);
CREATE TABLE community_snapshots (
 id uuid PRIMARY KEY, created_at timestamptz NOT NULL,
 owner_id uuid NOT NULL REFERENCES users(id), post_id uuid NOT NULL,
 version integer NOT NULL, status varchar(16) NOT NULL,
 title varchar(200) NOT NULL, body_json jsonb NOT NULL, plain_text text NOT NULL,
 subject varchar(80) NOT NULL, post_type varchar(16) NOT NULL, tags jsonb NOT NULL,
 source_note_id uuid, source_revision integer, request_key uuid NOT NULL, request_hash varchar(64) NOT NULL,
 job_id uuid UNIQUE REFERENCES jobs(id), error_code varchar(64), recommendation_score integer NOT NULL,
 CONSTRAINT fk_community_snapshot_owner FOREIGN KEY(owner_id,post_id) REFERENCES community_posts(owner_id,id),
 CONSTRAINT uq_community_snapshot_owner UNIQUE(owner_id,id),
 CONSTRAINT uq_community_snapshot_version UNIQUE(post_id,version),
 CONSTRAINT uq_community_publish_request UNIQUE(owner_id,request_key),
 CONSTRAINT community_snapshot_version CHECK(version > 0),
 CONSTRAINT community_snapshot_status CHECK(status IN ('preparing','ready','failed','cancelled')),
 CONSTRAINT community_snapshot_score CHECK(recommendation_score BETWEEN 0 AND 3),
 CONSTRAINT community_snapshot_type CHECK(post_type IN ('note','question','experience','resource'))
);
CREATE INDEX ix_community_snapshots_owner_id ON community_snapshots(owner_id);
ALTER TABLE community_posts ADD CONSTRAINT fk_community_live_snapshot FOREIGN KEY(live_snapshot_id) REFERENCES community_snapshots(id);
ALTER TABLE community_posts ADD CONSTRAINT fk_community_preparing_snapshot FOREIGN KEY(preparing_snapshot_id) REFERENCES community_snapshots(id);
CREATE TABLE community_assets (
 id uuid PRIMARY KEY, created_at timestamptz NOT NULL,
 owner_id uuid NOT NULL REFERENCES users(id), snapshot_id uuid NOT NULL, position integer NOT NULL,
 filename varchar(200) NOT NULL, mime varchar(64) NOT NULL, width integer, height integer,
 object_key varchar(256) NOT NULL UNIQUE, original_object_key varchar(256) NOT NULL UNIQUE,
 source_object_key varchar(256), source_original_object_key varchar(256),
 bytes bigint NOT NULL, original_bytes bigint NOT NULL, sha256 varchar(64), original_sha256 varchar(64) NOT NULL,
 status varchar(16) NOT NULL,
 CONSTRAINT fk_community_asset_owner FOREIGN KEY(owner_id,snapshot_id) REFERENCES community_snapshots(owner_id,id),
 CONSTRAINT uq_community_asset_position UNIQUE(snapshot_id,position),
 CONSTRAINT community_asset_sizes CHECK(position >= 0 AND bytes > 0 AND original_bytes > 0),
 CONSTRAINT community_asset_status CHECK(status IN ('pending','ready'))
);
CREATE INDEX ix_community_assets_owner_id ON community_assets(owner_id);
CREATE TABLE community_comments (
 id uuid PRIMARY KEY, created_at timestamptz NOT NULL,
 owner_id uuid NOT NULL REFERENCES users(id), post_id uuid NOT NULL REFERENCES community_posts(id),
 body varchar(4000) NOT NULL, request_key uuid NOT NULL, deleted_at timestamptz,
 CONSTRAINT uq_community_comment_request UNIQUE(owner_id,request_key)
);
CREATE INDEX ix_community_comments_owner_id ON community_comments(owner_id);
CREATE INDEX ix_community_comments_post_id ON community_comments(post_id);
CREATE TABLE community_reactions (
 id uuid PRIMARY KEY, created_at timestamptz NOT NULL,
 owner_id uuid NOT NULL REFERENCES users(id), post_id uuid NOT NULL REFERENCES community_posts(id),
 kind varchar(16) NOT NULL,
 CONSTRAINT uq_community_reaction UNIQUE(owner_id,post_id,kind),
 CONSTRAINT community_reaction_kind CHECK(kind IN ('like','bookmark'))
);
CREATE INDEX ix_community_reactions_owner_id ON community_reactions(owner_id);
CREATE INDEX ix_community_reactions_post_id ON community_reactions(post_id);
CREATE TABLE community_reports (
 id uuid PRIMARY KEY, created_at timestamptz NOT NULL,
 owner_id uuid NOT NULL REFERENCES users(id), post_id uuid NOT NULL REFERENCES community_posts(id),
 reason varchar(500) NOT NULL, request_key uuid NOT NULL, status varchar(16) NOT NULL,
 resolved_by uuid REFERENCES users(id), resolution varchar(500), resolved_at timestamptz,
 CONSTRAINT uq_community_report_request UNIQUE(owner_id,request_key),
 CONSTRAINT community_report_status CHECK(status IN ('pending','dismissed','hidden'))
);
CREATE INDEX ix_community_reports_owner_id ON community_reports(owner_id);
CREATE INDEX ix_community_reports_post_id ON community_reports(post_id);
""")


def downgrade():
    op.drop_constraint("fk_community_live_snapshot", "community_posts", type_="foreignkey")
    op.drop_constraint("fk_community_preparing_snapshot", "community_posts", type_="foreignkey")
    for table in ("community_reports", "community_reactions", "community_comments", "community_assets", "community_snapshots", "community_posts"):
        op.drop_table(table)
