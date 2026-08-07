# =============================================================================
# Tools service — tools/
# =============================================================================
# WHAT: The stateless "utility tools" service. A single container that hosts the
#       tools that don't belong to any stateful domain module — web search,
#       headless-browser page reading, YouTube transcripts, cheapest flights,
#       city traffic — and exposes them to every agent via the frozen Module gRPC
#       contract (RegisterTools/CallTool), same as the domain modules.
#
# WHY a top-level service (not under modules/, not inside Brain): the domain
#       modules (mentor/tracker/therapist) own DATA + a domain; these tools own
#       neither — they're stateless utilities grouped by being tools, not by a
#       domain. Keeping them in one dedicated service keeps Brain (the critical
#       gateway) lean and isolates the flaky/heavy headless browser out of it.
#
# WHY one dir per tool (plugin layout): each tool is a self-contained folder that
#       exposes a `TOOL` object; `loader.py` walks the package and registers
#       whatever it finds. Adding a tool = drop in a new dir. Genuinely shared
#       low-level services (the browser, used by two tools) live in tools/shared.
#
# HOW it runs: `python -m tools.main` (the `tools` service in docker-compose).
#       Brain discovers it via BRAIN_MODULES="...,tools=tools:9105".
# =============================================================================
