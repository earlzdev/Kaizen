# =============================================================================
# Tools service entrypoint — tools/main.py
# =============================================================================
# WHAT: Boots the tools service. Stateless (no DB): load every tool via the
#       loader and serve the Module gRPC contract until killed.
#
# WHY it warns on a missing flight token: cheapest_flights needs a Travelpayouts
#       token; without it that one tool returns an error. The others work
#       regardless, so this is a warning, not a fatal.
#
# HOW it runs: `python -m tools.main` (the `tools` service in docker-compose).
#       Brain discovers it via BRAIN_MODULES="...,tools=tools:9105".
# =============================================================================

import asyncio
import logging

import grpc

from infra.modkit import ModuleServicer
from infra.proto.gen import module_pb2_grpc
from tools.config import settings
from tools.loader import load_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def serve() -> None:
    logger.info("Tools service starting...")
    if not settings.travelpayouts_token:
        logger.warning("TRAVELPAYOUTS_TOKEN empty — cheapest_flights will return an error.")

    servicer = ModuleServicer(settings.tools_module_name, load_tools())
    server = grpc.aio.server()
    module_pb2_grpc.add_ModuleServicer_to_server(servicer, server)
    server.add_insecure_port(settings.tools_bind_addr)
    await server.start()
    logger.info(
        "Tools service serving Module gRPC on %s (%d tools)",
        settings.tools_bind_addr,
        len(servicer.tools),
    )
    try:
        await server.wait_for_termination()
    except asyncio.CancelledError:
        await server.stop(grace=5)
        raise


if __name__ == "__main__":
    asyncio.run(serve())
