"""Freeze bounded learning activity facts and owner composite foreign keys."""
from alembic import op
import sqlalchemy as sa

revision = '84c6e9b302df'
down_revision = 'f73b5d8a201c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('learning_events',
        sa.Column('owner_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('note_id', sa.Uuid(), nullable=True),
        sa.Column('card_id', sa.Uuid(), nullable=True),
        sa.Column('quiz_id', sa.Uuid(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('owner_id', 'event_id', name='uq_learning_event_request'),
        sa.ForeignKeyConstraint(['owner_id', 'note_id'], ['notes.owner_id', 'notes.id'], name='fk_learning_event_note_owner'),
        sa.ForeignKeyConstraint(['owner_id', 'card_id'], ['learning_cards.owner_id', 'learning_cards.id'], name='fk_learning_event_card_owner'),
        sa.ForeignKeyConstraint(['owner_id', 'quiz_id'], ['learning_quizzes.owner_id', 'learning_quizzes.id'], name='fk_learning_event_quiz_owner'),
        sa.CheckConstraint("num_nonnulls(note_id,card_id,quiz_id) = 1 AND ((kind = 'reading' AND note_id IS NOT NULL) OR (kind = 'card_practice' AND card_id IS NOT NULL) OR (kind = 'quiz' AND quiz_id IS NOT NULL) OR kind = 'review')", name='learning_event_source_kind'),
        sa.CheckConstraint("ended_at > started_at AND ended_at <= started_at + interval '60 seconds'", name='learning_event_duration'))
    op.create_index('ix_learning_events_owner_time', 'learning_events', ['owner_id', 'started_at', 'ended_at'])


def downgrade():
    op.drop_table('learning_events')
