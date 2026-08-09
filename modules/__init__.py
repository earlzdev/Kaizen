# =============================================================================
# Modules — modules/
# =============================================================================
# WHAT: Namespace for the v2 capability modules (Наставник/mentor, Трекер/tracker,
#       Психолог/therapist). Each is an independent gRPC server with its own
#       logical DB that the Brain proxies tool calls to.
#
# WHY a package of its own: the plan (docs/plans/kaizen-v2-rollout.md) keeps
#       modules behind a single frozen gRPC contract so Brain talks to all of
#       them the same way. Grouping them here mirrors the mono-repo layout in the
#       plan and keeps them separate from the v1 monolith under app/.
#
# HOW: Phase 0 contains only `modules/stub` — a throwaway HealthService server
#       that proves the Brain -> module pipe. Real modules land from Phase 4.
# =============================================================================
