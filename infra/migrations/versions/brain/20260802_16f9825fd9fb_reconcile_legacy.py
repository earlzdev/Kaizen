"""reconcile a database that predates migrations

Revision ID: 16f9825fd9fb
Revises: b0c6588e924f
Created: 2026-08-02

No-op on a database this chain created: the baseline just built every table
from the same models, so nothing is missing. On a database that predates
migrations it adds the columns `metadata.create_all` could never add — the
reason this project moved to migrations at all.
"""
from alembic import op

from infra.migrations import drift, registry

revision = '16f9825fd9fb'
down_revision = 'b0c6588e924f'
branch_labels = None
depends_on = None

SERVICE = 'brain'


def upgrade() -> None:
    drift.add_missing_columns(op.get_bind(), registry.metadata_of(SERVICE))


def downgrade() -> None:
    # Deliberately empty: this revision only ever ADDS columns that the models
    # declare. Dropping them on downgrade would delete data the running code
    # writes, to "undo" a repair.
    pass
