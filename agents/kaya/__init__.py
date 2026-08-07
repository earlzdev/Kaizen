# =============================================================================
# Кая — agents/kaya/
# =============================================================================
# WHAT: The text agent (Telegram). Кая = agents.core + a Telegram connector + her
#       soul.md + her own local history DB + a Brain token. She holds NO tools
#       or memory of her own — those come from Brain over MCP.
#
# WHY this is a port, not a rewrite (principle 3): the dialogue loop, memory and
#       tools already exist. Кая reuses agents.core's loop and reaches Brain for
#       tools + shared memory; only her connector (the existing aiogram surface)
#       and her persona are agent-specific. Her local DB stores just the
#       conversation window — the shared "memory about me" lives in Brain.
#
# HOW it runs: `python -m agents.kaya.main` (the `kaya` service in
#       docker-compose). It creates/migrates Кая's DB, builds the Agent, and
#       polls Telegram; each message becomes agent.reply(text).
# =============================================================================
