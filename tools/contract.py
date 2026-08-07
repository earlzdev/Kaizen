# =============================================================================
# Tool definition — tools/contract.py
# =============================================================================
# WHAT: Re-exports the shared ToolDef (and ToolResult) from infra.modkit so
#       every tool folder keeps its one-line import:
#       `from tools.contract import ToolDef`.
#
# WHY a shim instead of deleting this module: eight tool folders import from
#       here; the type itself moved to infra/modkit (Step 5 of
#       ARCHITECTURE_REVIEW.md) so tools/, mentor and tracker share ONE
#       definition instead of three copies. Same class object — isinstance
#       checks in the loader keep working.
#
# HOW: in a tool's dir, `tool.py` builds and exports `TOOL = ToolDef(...)`.
#      Handlers may return str (legacy, "Error:" prefix = error) or ToolResult.
# =============================================================================

from infra.modkit import ToolDef, ToolResult

__all__ = ["ToolDef", "ToolResult"]
