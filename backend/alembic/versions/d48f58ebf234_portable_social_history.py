"""Inert portable M3 account histories and owned archived assets."""
from alembic import op
revision = "d48f58ebf234"
down_revision = "c37f47dae123"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("\nCREATE TABLE social_archive_records (\n\towner_id UUID NOT NULL, \n\tkind VARCHAR(32) NOT NULL, \n\tsource_id UUID NOT NULL, \n\tpayload JSONB NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_social_archive_owner_id UNIQUE (owner_id, id), \n\tCONSTRAINT social_archive_kind CHECK (kind IN ('profile','friendship','follow','note_grant','chat_thread','chat_message','chat_attachment','community_post','community_snapshot','community_comment','community_reaction','community_report')), \n\tFOREIGN KEY(owner_id) REFERENCES users (id)\n)\n\n")
    op.execute("\nCREATE TABLE social_archive_assets (\n\towner_id UUID NOT NULL, \n\trecord_id UUID NOT NULL, \n\tsource_id UUID NOT NULL, \n\trole VARCHAR(16) NOT NULL, \n\tobject_key VARCHAR(256) NOT NULL, \n\tfilename VARCHAR(200) NOT NULL, \n\tmime VARCHAR(64) NOT NULL, \n\tbytes BIGINT NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_social_archive_asset_owner FOREIGN KEY(owner_id, record_id) REFERENCES social_archive_records (owner_id, id), \n\tCONSTRAINT uq_social_archive_asset_source UNIQUE (owner_id, record_id, source_id, role), \n\tCONSTRAINT social_archive_asset_role CHECK (role IN ('original','reading','attachment')), \n\tCONSTRAINT social_archive_asset_size CHECK (bytes > 0 AND bytes <= 52428800), \n\tFOREIGN KEY(owner_id) REFERENCES users (id), \n\tUNIQUE (object_key)\n)\n\n")
    op.execute('CREATE INDEX ix_social_archive_owner_source ON social_archive_records (owner_id, kind, source_id)')
    op.execute('CREATE INDEX ix_social_archive_records_owner_id ON social_archive_records (owner_id)')
    op.execute('CREATE INDEX ix_social_archive_assets_owner_id ON social_archive_assets (owner_id)')
    op.execute('CREATE INDEX ix_social_archive_assets_record_id ON social_archive_assets (record_id)')

def downgrade():
    op.execute('DROP TABLE social_archive_assets')
    op.execute('DROP TABLE social_archive_records')
