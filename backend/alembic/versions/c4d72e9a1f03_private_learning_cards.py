"""Frozen DDL for private decks, cards and single-attempt generation receipts."""
from alembic import op

revision = "c4d72e9a1f03"
down_revision = "b81d4f0a6c32"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("\nCREATE TABLE learning_decks (\n\towner_id UUID NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_learning_deck_owner UNIQUE (owner_id, id), \n\tCONSTRAINT learning_deck_state CHECK (revision > 0 AND status IN ('active','archived')), \n\tFOREIGN KEY(owner_id) REFERENCES users (id)\n)\n\n")
    op.execute('CREATE INDEX ix_learning_decks_owner ON learning_decks (owner_id, updated_at)')
    op.execute("\nCREATE TABLE card_generations (\n\towner_id UUID NOT NULL, \n\tdeck_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\trequest_key UUID NOT NULL, \n\trequest_hash VARCHAR(64) NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tcount INTEGER NOT NULL, \n\tsource JSONB NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\terror_code VARCHAR(64), \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_card_generation_owner UNIQUE (owner_id, id), \n\tCONSTRAINT uq_card_generation_request UNIQUE (owner_id, request_key), \n\tCONSTRAINT fk_generation_deck_owner FOREIGN KEY(owner_id, deck_id) REFERENCES learning_decks (owner_id, id), \n\tCONSTRAINT fk_generation_run_owner FOREIGN KEY(owner_id, run_id) REFERENCES ai_runs (owner_id, id), \n\tCONSTRAINT card_generation_state CHECK (status IN ('pending','succeeded','failed') AND count BETWEEN 1 AND 20), \n\tCONSTRAINT card_generation_kind CHECK (kind IN ('qa','cloze','occlusion','formula')), \n\tFOREIGN KEY(owner_id) REFERENCES users (id), \n\tUNIQUE (run_id)\n)\n\n")
    op.execute('CREATE INDEX ix_card_generations_owner ON card_generations (owner_id, created_at)')
    op.execute("\nCREATE TABLE learning_cards (\n\towner_id UUID NOT NULL, \n\tdeck_id UUID NOT NULL, \n\tkind VARCHAR(16) NOT NULL, \n\tfront TEXT NOT NULL, \n\tback TEXT NOT NULL, \n\tpayload JSONB, \n\tsource JSONB, \n\tstatus VARCHAR(16) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tgeneration_id UUID, \n\tgeneration_ordinal INTEGER, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_learning_card_owner UNIQUE (owner_id, id), \n\tCONSTRAINT uq_card_generation_ordinal UNIQUE (generation_id, generation_ordinal), \n\tCONSTRAINT fk_card_deck_owner FOREIGN KEY(owner_id, deck_id) REFERENCES learning_decks (owner_id, id), \n\tCONSTRAINT fk_card_generation_owner FOREIGN KEY(owner_id, generation_id) REFERENCES card_generations (owner_id, id), \n\tCONSTRAINT learning_card_state CHECK (revision > 0 AND status IN ('draft','confirmed','archived')), \n\tCONSTRAINT learning_card_kind CHECK (kind IN ('qa','cloze','occlusion','formula')), \n\tCONSTRAINT learning_card_size CHECK (length(front) BETWEEN 1 AND 8000 AND length(back) <= 8000), \n\tCONSTRAINT learning_card_generation_pair CHECK ((generation_id IS NULL AND generation_ordinal IS NULL) OR (generation_id IS NOT NULL AND generation_ordinal BETWEEN 0 AND 19)), \n\tFOREIGN KEY(owner_id) REFERENCES users (id)\n)\n\n")
    op.execute('CREATE INDEX ix_learning_cards_owner ON learning_cards (owner_id, deck_id, status, updated_at)')


def downgrade():
    op.drop_table("learning_cards")
    op.drop_table("card_generations")
    op.drop_table("learning_decks")
