# =============================================================================
# Tool loader — tools/loader.py
# =============================================================================
# WHAT: Auto-discovers the tools. It walks the `tools` package's subpackages and
#       collects every `TOOL` (a ToolDef) they export. Adding a tool = drop a new
#       folder with a `TOOL` in it; no registry edit needed.
#
# WHY reflection over a hand-maintained list: the plugin layout ("each tool in
#       its own dir") is only ergonomic if new dirs register themselves. This is
#       that mechanism — import each tool subpackage, read its `TOOL`.
#
# WHY it skips `shared` and private/underscore packages: `tools/shared` holds
#       low-level services (the browser) used BY tools, not tools themselves;
#       infrastructure modules here (config/contract/loader/server/main) are
#       modules, not subpackages, so iter_modules' ispkg filter already excludes
#       them — the name filter is belt-and-braces.
#
# HOW: `load_tools() -> list[ToolDef]` — used by server.py/main.py at startup.
# =============================================================================

import importlib
import logging
import pkgutil

import tools
from tools.contract import ToolDef

logger = logging.getLogger(__name__)

# Subpackages that are NOT tools (shared infra used by tools).
_NOT_TOOLS = {"shared"}


def load_tools() -> list[ToolDef]:
    """Import every tool subpackage and collect its exported TOOL."""
    found: list[ToolDef] = []
    for info in pkgutil.iter_modules(tools.__path__):
        if not info.ispkg or info.name in _NOT_TOOLS or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"tools.{info.name}")
        tool = getattr(module, "TOOL", None)
        if tool is None:
            logger.warning("Tool package 'tools.%s' has no TOOL — skipping", info.name)
            continue
        if not isinstance(tool, ToolDef):
            logger.warning("tools.%s.TOOL is not a ToolDef — skipping", info.name)
            continue
        found.append(tool)
    found.sort(key=lambda t: t.name)  # stable order (prompt-cache friendly)
    logger.info("Loaded %d tool(s): %s", len(found), [t.name for t in found])
    return found
