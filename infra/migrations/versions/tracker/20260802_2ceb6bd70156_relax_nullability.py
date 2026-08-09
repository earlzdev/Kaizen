"""relax NOT NULL where the models say optional

Revision ID: 2ceb6bd70156
Revises: 71e919e95ebd
Created: 2026-08-02

The companion to the reconcile revision: that one added columns the database
lacked, this one drops constraints the database has and the models do not.
Both exist because these databases predate migrations. No-op on anything this
chain created.
"""
from alembic import op

from infra.migrations import drift, registry

revision = '2ceb6bd70156'
down_revision = '71e919e95ebd'
branch_labels = None
depends_on = None

SERVICE = 'tracker'


def upgrade() -> None:
    drift.relax_nullability(op.get_bind(), registry.metadata_of(SERVICE))


def downgrade() -> None:
    # Deliberately empty: re-adding NOT NULL would fail on exactly the rows this
    # revision made possible.
    pass
