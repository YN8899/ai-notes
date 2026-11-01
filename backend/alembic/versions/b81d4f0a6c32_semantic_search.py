"""Frozen DDL for versioned private semantic indexes and single-attempt receipts."""
from alembic import op

revision = "b81d4f0a6c32"
down_revision = "7c0a9d2e4f61"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("\nCREATE TABLE semantic_indexes (\n\towner_id UUID NOT NULL, \n\trequest_key UUID NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tconfig JSONB NOT NULL, \n\tmanifest_hash VARCHAR(64) NOT NULL, \n\tsource_count INTEGER NOT NULL, \n\tchunk_count INTEGER NOT NULL, \n\tcompleted_chunks INTEGER NOT NULL, \n\tusage JSONB NOT NULL, \n\terror_code VARCHAR(64), \n\tjob_id UUID NOT NULL, \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_semantic_index_request UNIQUE (owner_id, request_key), \n\tCONSTRAINT semantic_index_status CHECK (status IN ('queued','building','ready','active','failed')), \n\tCONSTRAINT semantic_index_counts CHECK (chunk_count BETWEEN 1 AND 500 AND completed_chunks BETWEEN 0 AND chunk_count), \n\tFOREIGN KEY(owner_id) REFERENCES users (id), \n\tUNIQUE (job_id), \n\tFOREIGN KEY(job_id) REFERENCES jobs (id)\n)\n\n")
    op.execute("CREATE UNIQUE INDEX uq_semantic_active ON semantic_indexes (owner_id) WHERE status = 'active'")
    op.execute("CREATE UNIQUE INDEX uq_semantic_building ON semantic_indexes (owner_id) WHERE status = 'queued' OR status = 'building'")
    op.execute('\nCREATE TABLE semantic_chunks (\n\towner_id UUID NOT NULL, \n\tindex_id UUID NOT NULL, \n\tordinal INTEGER NOT NULL, \n\tsource_key VARCHAR(160) NOT NULL, \n\tsource_hash VARCHAR(64) NOT NULL, \n\tsource JSONB NOT NULL, \n\tstart INTEGER NOT NULL, \n\t"end" INTEGER NOT NULL, \n\ttext TEXT NOT NULL, \n\tdimensions INTEGER NOT NULL, \n\tembedding vector, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_semantic_chunk_ordinal UNIQUE (index_id, ordinal), \n\tCONSTRAINT semantic_chunk_range CHECK ("start" >= 0 AND "end" > "start" AND "end" - "start" = length(text) AND length(text) <= 800), \n\tCONSTRAINT semantic_chunk_dimensions CHECK (embedding IS NULL OR vector_dims(embedding) = dimensions), \n\tCONSTRAINT semantic_chunk_dimension_supported CHECK (dimensions IN (64,128,256,512,768,1024,1536,2048)), \n\tFOREIGN KEY(owner_id) REFERENCES users (id), \n\tFOREIGN KEY(index_id) REFERENCES semantic_indexes (id)\n)\n\n')
    op.execute('CREATE INDEX ix_semantic_chunks_index_id ON semantic_chunks (index_id)')
    op.execute("\nCREATE TABLE semantic_queries (\n\towner_id UUID NOT NULL, \n\trequest_key UUID NOT NULL, \n\trequest_hash VARCHAR(64) NOT NULL, \n\tindex_id UUID NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tpayload JSONB NOT NULL, \n\tusage JSONB NOT NULL, \n\thits JSONB NOT NULL, \n\terror_code VARCHAR(64), \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_semantic_query_request UNIQUE (owner_id, request_key), \n\tCONSTRAINT semantic_query_status CHECK (status IN ('running','succeeded','failed')), \n\tFOREIGN KEY(owner_id) REFERENCES users (id), \n\tFOREIGN KEY(index_id) REFERENCES semantic_indexes (id)\n)\n\n")
    op.execute("\nCREATE TABLE semantic_calls (\n\tindex_id UUID NOT NULL, \n\tbatch INTEGER NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tusage JSONB NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_semantic_call_batch UNIQUE (index_id, batch), \n\tCONSTRAINT semantic_call_status CHECK (status IN ('started','succeeded','failed')), \n\tFOREIGN KEY(index_id) REFERENCES semantic_indexes (id)\n)\n\n")


def downgrade():
    op.drop_table('semantic_calls')
    op.drop_table('semantic_queries')
    op.drop_table('semantic_chunks')
    op.drop_table('semantic_indexes')
    # Preserve the shared vector extension.
