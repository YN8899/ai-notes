"""Private preferences and explicit aggregate-only learning analysis."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '18cd92cf3678'
down_revision = '07bc81be2567'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_home_preferences',
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('preferences', postgresql.JSONB(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('owner_id'), sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.CheckConstraint('revision > 0', name='home_preferences_revision'))
    op.create_table('learning_analysis_previews',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('auth_session_id', sa.Uuid(), nullable=False),
        sa.Column('preferences_revision', sa.Integer(), nullable=False),
        sa.Column('summary', postgresql.JSONB(), nullable=False),
        sa.Column('input_messages', postgresql.JSONB(), nullable=False),
        sa.Column('provider', sa.String(32), nullable=False),
        sa.Column('model', sa.String(256), nullable=False),
        sa.Column('provider_snapshot', postgresql.JSONB(), nullable=False),
        sa.Column('preview_hash', sa.String(64), nullable=False),
        sa.Column('input_bytes', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'), sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.CheckConstraint('input_bytes > 0 AND input_bytes <= 32000 AND preferences_revision > 0', name='analysis_preview_bounds'))
    op.create_index('ix_learning_analysis_previews_owner_id', 'learning_analysis_previews', ['owner_id'])
    op.create_index('ix_learning_analysis_previews_expires_at', 'learning_analysis_previews', ['expires_at'])
    op.create_table('learning_analyses',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('request_key', sa.Uuid(), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('preview_hash', sa.String(64), nullable=False),
        sa.Column('snapshot', postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint('id'), sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.ForeignKeyConstraint(['owner_id', 'run_id'], ['ai_runs.owner_id', 'ai_runs.id'], name='fk_learning_analysis_run_owner'),
        sa.UniqueConstraint('owner_id', 'request_key', name='uq_learning_analysis_request'),
        sa.UniqueConstraint('run_id', name='uq_learning_analysis_run'))
    op.create_index('ix_learning_analyses_owner_id', 'learning_analyses', ['owner_id'])


def downgrade():
    # User data must not be silently destroyed by a downgrade.
    for table in ('user_home_preferences', 'learning_analyses'):
        if op.get_bind().scalar(sa.text('SELECT EXISTS (SELECT 1 FROM ' + table + ')')):
            raise RuntimeError('personalization_downgrade_unsafe: restore a compatible backup')
    op.drop_table('learning_analyses')
    op.drop_table('learning_analysis_previews')
    op.drop_table('user_home_preferences')
