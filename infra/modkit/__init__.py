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
    """ModuleServicer подгружается ЛЕНИВО.

    WHY: он тянет grpcio, а DeliveryEvent — обычная pydantic-модель. Агенту
    нужен только контракт события (его приёмник разбирает пуши от Brain), и
    платить за это gRPC-стеком в своём образе он не должен: у голосового
    Кузи это лишний пакет, которым он никогда не пользуется, — и импорт
    падал на его образе, где grpcio нет. Модули, которым сервисер нужен,
    получают его тем же `from infra.modkit import ModuleServicer`.
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
