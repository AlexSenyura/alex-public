from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0001_init'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False, server_default='user'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)

    op.create_table(
        'snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_snapshots_id'), 'snapshots', ['id'], unique=False)

    op.create_table(
        'snapshot_videos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('snapshot_id', sa.Integer(), nullable=False),
        sa.Column('video_id', sa.String(length=32), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('channel_title', sa.String(length=255), nullable=False),
        sa.Column('keyword', sa.String(length=255), nullable=False),
        sa.Column('published_at', sa.DateTime(), nullable=False),
        sa.Column('duration_min', sa.Float(), nullable=False),
        sa.Column('views', sa.Integer(), nullable=False),
        sa.Column('views_per_day', sa.Float(), nullable=False),
        sa.Column('engagement_pct', sa.Float(), nullable=False),
        sa.Column('subs', sa.Integer(), nullable=True),
        sa.Column('views_to_subs', sa.Float(), nullable=True),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('rating', sa.Float(), nullable=False),
        sa.Column('trend_score', sa.Float(), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('thumb_url', sa.String(length=500), nullable=True),
        sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(['snapshot_id'], ['snapshots.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_snapshot_videos_id'), 'snapshot_videos', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_snapshot_videos_id'), table_name='snapshot_videos')
    op.drop_table('snapshot_videos')
    op.drop_index(op.f('ix_snapshots_id'), table_name='snapshots')
    op.drop_table('snapshots')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
