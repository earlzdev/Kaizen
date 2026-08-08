# =============================================================================
# Agent Core — agents/core/
# =============================================================================
# WHAT: The reusable library every v2 agent (Кая, Кузя) is built from. It holds
#       the parts that are identical across agents:
#         - the Claude tool-use loop (loop.py)
#         - an LLM client (llm.py) — the ONLY place the Anthropic SDK is used
#         - an MCP client to Brain (mcp_client.py) — tools + shared memory
#         - soul.md loading (soul.py) — the agent's identity/persona
#         - a local conversation-history seam (history.py) — each agent stores
#           its OWN dialogue in its OWN DB; the lib only defines the interface
#         - the Agent facade (agent.py) that ties it together: reply(text)->text
#
# WHY a library and not code inside each agent: the plan builds ONE agent core
#       and reuses it (principle 3 — we port Кая over, we don't rewrite her). An agent
#       becomes: agents.core + a connector (Telegram/voice) + a soul + a token.
#       Everything agent-specific is injected; nothing app/* is imported, so the
#       lib is independent of the v1 monolith and of any one agent.
#
# WHY the Anthropic SDK is confined to llm.py: CLAUDE.md's strict stack allows
#       the Anthropic SDK only inside agents (Agent Core). The loop depends on an
#       LLMClient Protocol, so it stays provider-agnostic and unit-testable with
#       a fake client — the SDK is an implementation detail behind the seam.
#
# HOW an agent uses it (see agents/kaya in the next chunk):
#       client  = AnthropicClient(api_key, model)
#       brain   = BrainMCPClient(brain_url, agent_token)
#       agent   = Agent(soul=load_soul(path), llm=client, brain=brain,
#                       history=my_history)
#       reply   = await agent.reply("hi")
# =============================================================================

from agents.core.agent import Agent
from agents.core.cli import ClaudeCliRunner
from agents.core.enroll import CredentialStore, EnrollmentClient, FileCredentialStore
from agents.core.history import History, InMemoryHistory
from agents.core.llm import AnthropicClient, AssistantTurn, LLMClient, ToolCall
from agents.core.loop import AgentLoop, ToolSource
from agents.core.mcp_client import BrainMCPClient
from agents.core.runner import RunResult, Runner, TurnUsage
from agents.core.soul import load_soul

__all__ = [
    "Agent",
    "AgentLoop",
    "AnthropicClient",
    "AssistantTurn",
    "BrainMCPClient",
    "ClaudeCliRunner",
    "CredentialStore",
    "EnrollmentClient",
    "FileCredentialStore",
    "History",
    "InMemoryHistory",
    "LLMClient",
    "RunResult",
    "Runner",
    "ToolCall",
    "ToolSource",
    "TurnUsage",
    "load_soul",
]
