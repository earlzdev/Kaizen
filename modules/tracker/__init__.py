# =============================================================================
# Трекер (tracker) module — modules/tracker/
# =============================================================================
# WHAT: The STANDALONE v2 tracker module (Phase 9). It owns project/agent/task
#       coordination end to end: its own database, the poller-facing HTTP API
#       (register/delegate/claim/report + dashboard), AND the frozen Module gRPC
#       contract (RegisterTools/CallTool) so agents delegate tasks through Brain.
#
# WHY standalone now (was a thin HTTP adapter over app/tracker in Phase 5): the
#       v1 monolith is being deleted, so the tracker's store/models/API were
#       ported here with their OWN DB — no more app/* dependency. External
#       pollers keep speaking the SAME HTTP contract with their per-project
#       tokens; agents get a gRPC face on the same store.
#
# HOW it runs: `python -m modules.tracker.main` (the `tracker` service in
#       docker-compose) — serves HTTP (pollers) and gRPC (Brain) concurrently.
#       Brain discovers it via BRAIN_MODULES="...,tracker=tracker:9103".
#
# Files: config.py (own DB + tokens + binds), session.py (own engine), models.py
#       + store.py + api.py + panel.py (tracker internals), tools.py
#       (store-backed agent tools), main.py (boot; wires the shared
#       infra.modkit ModuleServicer to the gRPC face).
# =============================================================================
