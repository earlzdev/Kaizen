# =============================================================================
# Brain HTTP request models — brain/api_models.py
# =============================================================================
# WHAT: Pydantic models for every JSON body Brain's HTTP surface accepts
#       (admin agent mint, delivery registration, access rules, enrollment).
#       The MCP envelope itself stays dict-shaped — it is JSON-RPC, validated
#       by its own dispatch.
#
# WHY typed models instead of .get() chains (Step 7 of ARCHITECTURE_REVIEW.md):
#       every request contract used to exist only as a chain of
#       `(body.get("slug") or "").strip()` in the handler — invisible to the
#       reader, unenforced for the caller, and inconsistent about garbage input
#       (a non-string secret 500'd one route and passed another). One model per
#       body makes the contract readable in one place and turns malformed input
#       into a uniform 400.
#
# WHY str_strip_whitespace + min_length: the old handlers stripped and then
#       rejected empties; the models keep exactly that behavior declaratively.
#
# HOW: `model, err = await parse_body(request, EnrollRequest)` in server.py —
#      err is a ready 400 response when the body doesn't fit.
# =============================================================================

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    """Common config: strip strings, ignore unknown keys (lenient reads —
    additive client changes must not break older Brains)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


class CreateAgentRequest(_Body):
    """POST /admin/agents — mint an agent by hand."""

    slug: str = Field(min_length=1, max_length=64)
    delivery_addr: str | None = None


class SetDeliveryRequest(_Body):
    """POST /agent/delivery — an agent registers where Brain can push to it."""

    delivery_addr: str = Field(min_length=1, max_length=255)


class AddAccessRuleRequest(_Body):
    """POST /admin/access — one allow/deny exception for an agent."""

    agent_id: int
    module: str | None = None
    tool: str | None = None
    allowed: bool = False


class EnrollRequest(_Body):
    """POST /enroll — an agent asks to connect (pairing)."""

    slug: str = Field(min_length=1, max_length=64)
    secret: str = Field(min_length=1)
    enroll_token: str = ""


class EnrollStatusRequest(_Body):
    """POST /enroll/status — the secret-authenticated decision poll."""

    slug: str = Field(min_length=1, max_length=64)
    secret: str = Field(min_length=1)


class ModuleEventRequest(_Body):
    """POST /event — a module asks Brain to push a message to an agent.

    `kind` is the MODULE's event kind (e.g. "question", "report"), kept for
    logging and for future routing; what reaches the agent is always the same
    delivery event shape. `agent` names the target slug and defaults to the
    configured delivery agent (Кая), so a module never has to know which
    agents exist — let alone where they live.
    """

    kind: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=8000)
    agent: str = ""


class TunnelMessageRequest(_Body):
    """POST /tunnel/message — one turn of a live direct agent-to-agent
    tunnel, logged.

    Separate from ModuleEventRequest even though both are module-to-Brain
    calls: /event is a one-shot notable-event ping, deliberately clipped to
    Telegram-message length; a tunnel writes on EVERY turn and must not lose
    text to that clip. `role` is who spoke this turn, not who receives it —
    there is no delivery decision here, only a transcript write.
    """

    directive_id: int
    project: str = Field(min_length=1, max_length=255)
    role: str = Field(pattern="^(owner|agent)$")
    text: str = Field(min_length=1, max_length=20000)
    agent_slug: str = ""
