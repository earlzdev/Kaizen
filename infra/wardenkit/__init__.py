# =============================================================================
# Warden kit — infra/wardenkit
# =============================================================================
# WHAT: The project-side library for the tracker v2 contract. A project that
#       wants a real agent fleet imports THIS and nothing else from Kaizen:
#         WardenServicer / serve   — the gRPC daemon the Hub dispatches to
#         DirectiveJob / JobResult — what a pipeline handler receives and returns
#         HubClient                — register / status / report / ask / heartbeat
#         TrackerFiles             — the docs/tracker/{task_id}/ file conventions
#         make_manifest            — build a ProjectManifest without protobuf
#         ClaudeRunner / CliRun    — the ONE supported way to run the `claude`
#                                    CLI: merged stderr, the answer captured on
#                                    success, quota detection, two timeouts,
#                                    token usage (CliUsage) parsed off the
#                                    result event
#         ConversationLog          — the bounded question/answer window that
#                                    makes `ask` cheap to run repeatedly
#         run_persona_turn         — status→run→status for one persona, the
#                                    one place every topology (solo or crew)
#                                    does a turn and reports its usage
#         run_conversation         — the `converse`-kind handler: a loop that
#                                    answers owner messages one at a time
#                                    until the Directive is Cancelled (the
#                                    "позови альфреда" tunnel)
#         build_context            — a fresh docs/decisions.md + CHANGELOG +
#                                    git-log slice for one prompt (no new
#                                    persistent store — the repo IS memory)
#
# WHY a shared kit and not a copy per project (mirrors infra/modkit's role): the
#       fiddly parts — the atomic accept/at-capacity answer, heartbeating for the
#       whole life of a job, self-healing re-enrollment, the exact Handoff
#       filename every persona greps for — are the same in every project and
#       wrong in a subtly different way in each hand-rolled copy. One
#       implementation means every project's agents genuinely agree on the format.
#
# WHY it lives in infra/: infra is the ONE package every service may import
#       (like infra.proto.gen — the generated contract). Service isolation is
#       preserved: projects still never import brain/, modules/ or agents/.
#
# WHY its only dependencies are grpcio + protobuf: this package is copied or
#       pip-installed into OTHER repositories, whose toolchain may be Go or Rust
#       with a thin Python sidecar. Every dependency here becomes theirs, so the
#       flat-YAML reader is hand-rolled and there is no pydantic, no aiohttp, no
#       ORM. (protobuf comes from the committed generated stubs in
#       infra/proto/gen, not from this code directly — see the Dockerfile in
#       modules/tracker/example/dummy-project/, which is the tested proof of
#       the whole list.)
#
# HOW a project's warden.py looks, in full:
#       hub = HubClient("tracker:9104", token_path="/state/hub_token")
#       manifest = make_manifest("vpn", purpose="…", kinds=["develop", "fix"],
#                                max_concurrent=2, grpc_addr="vpn:9200")
#       await hub.enroll(manifest)
#       server = await serve(WardenServicer(manifest, run_pipeline, hub=hub,
#                                           repo_root="/repo"))
#       await server.wait_for_termination()   # serve() only STARTS it; without
#                                             # this the process exits at once
# =============================================================================

from infra.wardenkit.client import CHANNEL_OPTIONS, HubClient, HubUnauthenticated
from infra.wardenkit.clirunner import ClaudeRunner, CliRun, CliUsage
from infra.wardenkit.conversation import ConversationLog
from infra.wardenkit.conversemode import run_conversation
from infra.wardenkit.pipeline import run_persona_turn
from infra.wardenkit.repocontext import build_context
from infra.wardenkit.servicer import (
    REASON_AT_CAPACITY,
    REASON_UNSUPPORTED_KIND,
    DirectiveJob,
    JobResult,
    WardenServicer,
    make_manifest,
    serve,
)
from infra.wardenkit.trackerfiles import TrackerFiles, slugify

__all__ = [
    "CHANNEL_OPTIONS",
    "REASON_AT_CAPACITY",
    "REASON_UNSUPPORTED_KIND",
    "ClaudeRunner",
    "CliRun",
    "CliUsage",
    "ConversationLog",
    "DirectiveJob",
    "HubClient",
    "HubUnauthenticated",
    "JobResult",
    "TrackerFiles",
    "WardenServicer",
    "build_context",
    "make_manifest",
    "run_conversation",
    "run_persona_turn",
    "serve",
    "slugify",
]
