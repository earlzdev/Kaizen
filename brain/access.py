# =============================================================================
# Brain access-list — brain/access.py
# =============================================================================
# WHAT: Decides whether a given agent may call a given (module, tool). This is
#       the "стережёт доступ" half of Brain from the plan.
#
# WHY allow-by-default, deny точечно (Phase 2 rule): every agent may call every
#       tool UNLESS a rule says otherwise. A rule in access_rules is an
#       EXCEPTION carved out of that default. This keeps the common case
#       (a trusted agent) zero-config, and lets you clip a specific agent's
#       reach (e.g. Кузя loses "search by image") with one row.
#
# WHY "most specific rule wins": rules can target three scopes —
#       exact (module+tool) > whole-module (module, tool=NULL) > blanket
#       (module=NULL, tool=NULL). The narrowest matching rule decides, so an
#       allow on one tool can override a broader module deny, and vice versa.
#       For Brain's built-in tools (module is None) exact == blanket.
#
# HOW: `await AccessControl().is_allowed(agent_id, module, tool)` -> bool.
#       The MCP server calls this to FILTER tools/list and to GATE tools/call.
# =============================================================================

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from brain.db.models import AccessRule
from brain.db.session import get_session

logger = logging.getLogger(__name__)


class AccessControl:
    """Enforces the per-agent access-list (allow-by-default)."""

    def __init__(self, session_factory: async_sessionmaker | None = None) -> None:
        # None = the service's default DB; tests pass a scratch-DB sessionmaker.
        self._sessions = session_factory

    async def is_allowed(
        self, agent_id: int, module: str | None, tool: str
    ) -> bool:
        """True if `agent_id` may call `tool` (owned by `module`).

        Allow-by-default: with no matching rule, returns True. Otherwise the
        most specific matching rule's `allowed` flag decides."""
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(AccessRule).where(AccessRule.agent_id == agent_id)
            )
            rules = list(result.scalars().all())
        return self._decide(rules, module, tool)

    # ----- admin CRUD (the admin panel edits the access-list through these) --
    async def list_rules(self, agent_id: int) -> list[AccessRule]:
        """All exception rules for an agent, newest first."""
        async with get_session(self._sessions) as session:
            result = await session.execute(
                select(AccessRule)
                .where(AccessRule.agent_id == agent_id)
                .order_by(AccessRule.created_at.desc())
            )
            return list(result.scalars().all())

    async def add_rule(
        self, agent_id: int, module: str | None, tool: str | None, allowed: bool
    ) -> AccessRule:
        """Add one allow/deny exception. Empty module/tool are stored as NULL so
        the scope widens (whole-module or blanket)."""
        async with get_session(self._sessions) as session:
            rule = AccessRule(
                agent_id=agent_id,
                module=module or None,
                tool=tool or None,
                allowed=allowed,
            )
            session.add(rule)
            await session.flush()
            logger.info(
                "Access rule added: agent=%d module=%s tool=%s allowed=%s",
                agent_id, module, tool, allowed,
            )
            return rule

    async def delete_rule(self, rule_id: int) -> bool:
        """Remove one rule by id. Returns False if it doesn't exist."""
        async with get_session(self._sessions) as session:
            rule = await session.get(AccessRule, rule_id)
            if rule is None:
                return False
            await session.delete(rule)
            return True

    @staticmethod
    def _decide(rules: list[AccessRule], module: str | None, tool: str) -> bool:
        """Pure decision over already-loaded rules (kept separate so it is
        trivially unit-testable without a DB).

        Specificity score: exact match 3, whole-module 2, blanket 1. The
        highest-scoring matching rule wins; ties break toward DENY (the safer
        choice when two equally-specific rules disagree)."""
        best_score = 0
        decision = True  # allow-by-default
        for rule in rules:
            # A rule with a module set only applies to tools of that module.
            if rule.module is not None and rule.module != module:
                continue
            # A rule with a tool set only applies to that tool.
            if rule.tool is not None and rule.tool != tool:
                continue

            if rule.module is not None and rule.tool is not None:
                score = 3
            elif rule.module is not None or rule.tool is not None:
                score = 2
            else:
                score = 1

            if score > best_score or (score == best_score and not rule.allowed):
                best_score = score
                decision = rule.allowed
        return decision
