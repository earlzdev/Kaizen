"""add tracker_agent_usage

Revision ID: ce14cccb88a5
Revises: 2ceb6bd70156
Created: 2026-08-04

An append-only ledger of token/cost usage per persona turn (architecture §8,
"Scaling the fleet"). Unlike tracker_agent_status (overwritten — Status
duplicates the Handoff-file trail on purpose), usage has no other record
anywhere, so it is kept as history. Powers the panel's Analytics tab.
"""
from alembic import op
import sqlalchemy as sa

revision = 'ce14cccb88a5'
down_revision = '2ceb6bd70156'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'tracker_agent_usage',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('directive_id', sa.Integer(), nullable=False),
        sa.Column('agent_slug', sa.String(length=255), nullable=False),
        sa.Column('phase', sa.String(length=255), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
        sa.Column('cache_write_tokens', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['directive_id'], ['tracker_directives.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_tracker_agent_usage_directive_id'), 'tracker_agent_usage', ['directive_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_tracker_agent_usage_directive_id'), table_name='tracker_agent_usage')
    op.drop_table('tracker_agent_usage')
