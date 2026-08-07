"""reconcile a database that predates migrations

Revision ID: ef9bb46a7262
Revises: 6c208a2971ca
Created: 2026-08-02

No-op on a database this chain created: the baseline just built every table
from the same models, so nothing is missing. On a database that predates
migrations it adds the columns `metadata.create_all` could never add — the
reason this project moved to migrations at all.
"""
from alembic import op

from infra.migrations import drift, registry

revision = 'ef9bb46a7262'
down_revision = '6c208a2971ca'
branch_labels = None
depends_on = None

SERVICE = 'kaya'


def upgrade() -> None:
    drift.add_missing_columns(op.get_bind(), registry.metadata_of(SERVICE))


def downgrade() -> None:
    # Deliberately empty: this revision only ever ADDS columns that the models
    # declare. Dropping them on downgrade would delete data the running code
    # writes, to "undo" a repair.
    pass
