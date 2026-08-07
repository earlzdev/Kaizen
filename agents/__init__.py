# =============================================================================
# Agents — agents/
# =============================================================================
# WHAT: Namespace for the concrete v2 agents. Each subpackage is one agent built
#       from Agent Core: a connector (Telegram/voice) + a soul.md + its own local
#       history DB + a Brain token.
#
# WHY grouped here: mirrors the mono-repo layout in docs/plans/kaizen-v2-rollout.md
#       (/agents/kaya, /agents/kuzya). Agents share the agents.core library and a
#       single Brain; they differ only in connector, soul and access-list.
#
# HOW: Phase 3 ships `agents/kaya` (Telegram). `agents/kuzya` (voice, Phase 7)
#       lands later against the same Agent Core.
# =============================================================================
