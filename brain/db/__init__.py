# =============================================================================
# Brain DB package — brain/db/
# =============================================================================
# WHAT: Brain's own persistence layer: SQLAlchemy models (models.py) and the
#       async session factory (session.py) for Brain's dedicated logical
#       database.
#
# WHY separate from app/db: the plan gives Brain its own DB (no shared tables,
#       no cross-service JOINs). Brain's Base/metadata therefore live here and
#       are driven by Brain's own Alembic chain under brain/migrations/, wholly
#       independent of the monolith's alembic/.
#
# HOW: `from brain.db.models import Fact, Agent, ...`;
#      `from brain.db.session import get_session`.
# =============================================================================
