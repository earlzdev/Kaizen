"""relax NOT NULL where the models say optional

Revision ID: feffaef29216
Revises: ef9bb46a7262
Created: 2026-08-02

The companion to the reconcile revision: that one added columns the database
lacked, this one drops constraints the database has and the models do not.
Both exist because these databases predate migrations. No-op on anything this
chain created.
"""
from alembic import op

from infra.migrations import drift, registry

revision = 'feffaef29216'
down_revision = 'ef9bb46a7262'
branch_labels = None
depends_on = None

SERVICE = 'kaya'


def upgrade() -> None:
    drift.relax_nullability(op.get_bind(), registry.metadata_of(SERVICE))


def downgrade() -> None:
    # Deliberately empty: re-adding NOT NULL would fail on exactly the rows this
    # revision made possible.
    pass
