# =============================================================================
# Module kit — infra/modkit
# =============================================================================
# WHAT: The shared runtime for everything that serves tools over the frozen
#       Module gRPC contract: ToolDef (one tool's shape), ToolResult (a
#       structured outcome instead of the "Error:" string convention),
#       validate_args (arguments checked against the declared input_schema
#       BEFORE they reach a handler), and ModuleServicer (the one gRPC servicer
#       that used to exist as three byte-identical copies in tools/, mentor and
#       tracker).
#
# WHY a shared lib in infra/ and not a copy per service (Step 5 of
#       ARCHITECTURE_REVIEW.md): the copies had already drifted into a
#       three-place fix for every dispatch bug, and the "Error:" prefix
#       convention had no single owner. infra/ is the ONE package every service
#       may import (like infra.proto.gen — the generated contract), so sharing
#       runtime code here keeps service isolation intact: services still never
#       import each other.
#
# HOW: `from infra.modkit import ToolDef, ToolResult, ModuleServicer`.
# =============================================================================

from infra.modkit.tooldef import ToolDef, ToolHandler, ToolResult, to_result, validate_args
from infra.modkit.events import DeliveryEvent


def __getattr__(name: str):
    """ModuleServicer is loaded LAZILY.

    WHY: it pulls in grpcio, while DeliveryEvent is a plain pydantic model. An
    agent only needs the event contract (its receiver parses pushes from
    Brain), and shouldn't have to pay for a gRPC stack in its own image for
    that: for the voice agent Кузя it's a package it never uses, and the
    import used to fail on his image, which has no grpcio. Modules that do
    need the servicer get it through the same
    `from infra.modkit import ModuleServicer`.
    """
    if name == "ModuleServicer":
        from infra.modkit.servicer import ModuleServicer

        return ModuleServicer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DeliveryEvent",
    "ModuleServicer",
    "ToolDef",
    "ToolHandler",
    "ToolResult",
    "to_result",
    "validate_args",
]
