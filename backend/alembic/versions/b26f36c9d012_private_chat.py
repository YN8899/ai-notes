"""Durable private chat and one-use realtime handshakes."""
from alembic import op
revision = "b26f36c9d012"
down_revision = "a13f25b8c901"
branch_labels = None
depends_on = None

def upgrade():
    op.execute('\nCREATE TABLE chat_threads (\n\tuser_low_id UUID NOT NULL, \n\tuser_high_id UUID NOT NULL, \n\tlast_seq BIGINT NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT chat_thread_pair UNIQUE (user_low_id, user_high_id), \n\tCONSTRAINT chat_thread_order CHECK (user_low_id < user_high_id AND last_seq >= 0), \n\tFOREIGN KEY(user_low_id) REFERENCES users (id), \n\tFOREIGN KEY(user_high_id) REFERENCES users (id)\n)\n\n')
    op.execute('\nCREATE TABLE chat_messages (\n\tthread_id UUID NOT NULL, \n\tsender_id UUID NOT NULL, \n\tclient_message_id VARCHAR(64) NOT NULL, \n\tseq BIGINT NOT NULL, \n\tbody TEXT NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT chat_message_request UNIQUE (sender_id, client_message_id), \n\tCONSTRAINT chat_message_sequence UNIQUE (thread_id, seq), \n\tCONSTRAINT chat_message_positive_seq CHECK (seq > 0), \n\tFOREIGN KEY(thread_id) REFERENCES chat_threads (id), \n\tFOREIGN KEY(sender_id) REFERENCES users (id)\n)\n\n')
    op.execute('\nCREATE TABLE chat_read_cursors (\n\tthread_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tlast_read_seq BIGINT NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT chat_read_member UNIQUE (thread_id, user_id), \n\tCONSTRAINT chat_read_positive CHECK (last_read_seq >= 0), \n\tFOREIGN KEY(thread_id) REFERENCES chat_threads (id), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n')
    op.execute('\nCREATE TABLE realtime_tickets (\n\tuser_id UUID NOT NULL, \n\tsession_id UUID NOT NULL, \n\ttoken_hash VARCHAR(64) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tused_at TIMESTAMP WITH TIME ZONE, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(user_id) REFERENCES users (id), \n\tFOREIGN KEY(session_id) REFERENCES sessions (id), \n\tUNIQUE (token_hash)\n)\n\n')
    op.execute('\nCREATE TABLE chat_attachments (\n\tthread_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tmessage_id UUID, \n\tobject_key VARCHAR(256) NOT NULL, \n\tfilename VARCHAR(200) NOT NULL, \n\tmime VARCHAR(64) NOT NULL, \n\tbytes BIGINT NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\trequest_key VARCHAR(64) NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT chat_attachment_request UNIQUE (owner_id, request_key), \n\tCONSTRAINT chat_attachment_size CHECK (bytes > 0 AND bytes <= 10485760), \n\tFOREIGN KEY(thread_id) REFERENCES chat_threads (id), \n\tFOREIGN KEY(owner_id) REFERENCES users (id), \n\tFOREIGN KEY(message_id) REFERENCES chat_messages (id), \n\tUNIQUE (object_key)\n)\n\n')
    op.execute('CREATE INDEX ix_chat_threads_user_high_id ON chat_threads (user_high_id)')
    op.execute('CREATE INDEX ix_chat_threads_user_low_id ON chat_threads (user_low_id)')
    op.execute('CREATE INDEX ix_chat_messages_sender_id ON chat_messages (sender_id)')
    op.execute('CREATE INDEX ix_chat_messages_thread_id ON chat_messages (thread_id)')
    op.execute('CREATE INDEX ix_chat_read_cursors_thread_id ON chat_read_cursors (thread_id)')
    op.execute('CREATE INDEX ix_chat_read_cursors_user_id ON chat_read_cursors (user_id)')
    op.execute('CREATE INDEX ix_realtime_tickets_expires_at ON realtime_tickets (expires_at)')
    op.execute('CREATE INDEX ix_realtime_tickets_session_id ON realtime_tickets (session_id)')
    op.execute('CREATE INDEX ix_realtime_tickets_user_id ON realtime_tickets (user_id)')
    op.execute('CREATE INDEX ix_chat_attachments_message_id ON chat_attachments (message_id)')
    op.execute('CREATE INDEX ix_chat_attachments_owner_id ON chat_attachments (owner_id)')
    op.execute('CREATE INDEX ix_chat_attachments_thread_id ON chat_attachments (thread_id)')

def downgrade():
    op.execute('DROP TABLE chat_attachments')
    op.execute('DROP TABLE realtime_tickets')
    op.execute('DROP TABLE chat_read_cursors')
    op.execute('DROP TABLE chat_messages')
    op.execute('DROP TABLE chat_threads')
