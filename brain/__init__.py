# =============================================================================
# Brain — brain/
# =============================================================================
# WHAT: The central v2 service package. It is the single gateway between agents
#       and modules: MCP front door toward agents, gRPC client toward modules,
#       plus the shared "memory about me" and the per-agent access-list.
#
# WHY it exists as its own package: the v2 rollout (docs/plans/kaizen-v2-rollout.md)
#       makes Brain the one place that holds identity, memory and authz, so every
#       other service depends on it. It lives ALONGSIDE the v1 monolith in app/
#       (principle 4) until v2 catches up — nothing here touches the old bot.
#
# HOW: Phase 0 ships only `brain.main` (a gRPC self-test against the stub
#       module). The MCP server, tool router and Postgres-backed memory arrive
#       in later phases; see the rollout plan.
# =============================================================================
