# =============================================================================
# Tool usage block — agents/core/tool_usage.py
# =============================================================================
# WHAT: Renders the "how to use your tools" section of an agent's system prompt
#       from the usage notes the TOOLS themselves ship (ToolDef.usage), for
#       exactly the tools this agent can see.
#
# WHY the notes live with the tools and not in a soul (or one central file):
#       a usage example describes a tool's arguments, so it must move when the
#       tool moves. Keeping it in the tool's own definition makes adding a tool
#       a one-file change and keeps every agent's guidance in sync
#       automatically; a central catalogue would silently rot the moment a tool
#       changed its arguments. This module is only the RENDERER.
#
# WHY it is per-agent for free: Brain filters tools/list by the caller's
#       access-list, so an agent that may not call a tool never receives its
#       usage note — no "you can also do X" for something it would be denied.
#
# WHY it is deliberately small: the block ships in EVERY prompt. Only tools
#       with genuinely error-prone arguments declare a usage note; the rest say
#       nothing and cost nothing. An agent whose tools declare none gets no
#       block at all (not an empty heading).
#
# HOW: `render_tool_usage(await brain.usage_notes())` -> str ("" when empty),
#      appended to the stable head of the system prompt by agents/core/agent.py.
# =============================================================================

from collections.abc import Iterable

_HEADER = "## How to use your tools"

_PREAMBLE = (
    "Notes from the tools themselves — how to call them well. The tool list "
    "you already have says WHAT each tool is; this says how to get it right."
)


def render_tool_usage(notes: Iterable[tuple[str, str]]) -> str:
    """Render (tool_name, usage) pairs into a system-prompt block.

    Returns "" when no tool ships a note, so the caller can skip the section
    entirely. Tools are sorted by name: a stable order keeps the system
    prompt byte-identical between turns, which is what the API prompt cache
    needs (same reason brain's registry sorts tools/list)."""
    entries = sorted(
        ((name, usage.strip()) for name, usage in notes if usage and usage.strip()),
        key=lambda pair: pair[0],
    )
    if not entries:
        return ""
    lines = [_HEADER, "", _PREAMBLE, ""]
    for name, usage in entries:
        # Indent continuation lines so a multi-line note stays visually part
        # of its bullet rather than reading as a new instruction.
        body = usage.replace("\n", "\n  ")
        lines.append(f"- **{name}** — {body}")
    return "\n".join(lines)
