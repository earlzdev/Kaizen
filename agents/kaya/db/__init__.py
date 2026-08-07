# =============================================================================
# Кая DB package — agents/kaya/db/
# =============================================================================
# WHAT: Кая's local persistence: the `messages` model (models.py) and the async
#       session factory (session.py) for her own database.
#
# WHY separate and tiny: Кая's local DB stores ONLY her conversation window —
#       the shared "memory about me" lives in Brain, not here. One table, one
#       migration. Isolation mirrors Brain: her own Base, her own Alembic chain.
#
# HOW: `from agents.kaya.db.models import Message`;
#      `from agents.kaya.db.session import get_session`.
# =============================================================================
