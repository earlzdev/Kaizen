# =============================================================================
# Brain tunnel transcript — brain/tunnel.py
# =============================================================================
# WHAT: The write side of the "позови альфреда" tunnel's transcript — every
#       owner<->Warden turn, logged so Кая doesn't lose the thread while the
#       owner is talking directly to a project's agent.
#
# WHY logged in Brain and not left as a raw side-channel (the owner's own
#       decision, direct-but-logged): the dialogue goes straight through
#       (Кая -> tracker -> Warden and back, see modules/tracker/tools.py's
#       open_conversation/send_chat_message), but every turn also lands here
#       so Кая can catch up on what was said without holding the microphone
#       herself for the whole conversation.
#
# WHY not brain/episodes.py's EpisodeStore: see TunnelMessage's docstring in
#       brain/db/models.py — a tunnel is many small turns in one session, not
#       one coarse embedded exchange, and nobody searches it semantically.
#
# HOW: `TunnelStore().log(directive_id, project, role, text)` — called from
#      brain/server.py's POST /tunnel/message handler (brain/api_models.py's
#      TunnelMessageRequest is that route's body contract).
# =============================================================================

from sqlalchemy.ext.asyncio import async_sessionmaker

from brain.db.models import TunnelMessage
from brain.db.session import get_session

# A pathological turn (a Warden echoing a huge file) must not bloat a row —
# the transcript's own length cap already covers this at the request-body
# layer (TunnelMessageRequest), this is the belt to that suspenders.
_MAX_STORED_CHARS = 20000


class TunnelStore:
    """Append-only log of tunnel turns."""

    def __init__(self, session_factory: async_sessionmaker | None = None) -> None:
        self._sessions = session_factory

    async def log(
        self, directive_id: int, project: str, role: str, text: str, agent_slug: str = ""
    ) -> None:
        async with get_session(self._sessions) as session:
            session.add(
                TunnelMessage(
                    directive_id=directive_id,
                    project=project[:255],
                    agent_slug=agent_slug[:64],
                    role=role,
                    text=text[:_MAX_STORED_CHARS],
                )
            )


__all__ = ["TunnelStore"]
