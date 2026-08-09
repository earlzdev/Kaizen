# How does a project join the Kaizen tracker?

<!--
WHAT: The short path for connecting a new project to the tracker Hub so it can
      receive dispatched work and report status back.
WHY:  the full contract is already documented; what is missing is the two-minute
      "which tier do I want and what do I add to my repo" answer for someone
      standing in a fresh project.
STATUS: STUB — outline only. The authoritative source is
      docs/tracker-integration.md; this file should be the condensed pointer, not
      a second copy.
-->

Full contract: [`docs/tracker-integration.md`](../tracker-integration.md).
Architecture and vocabulary: [`docs/tracker-architecture.md`](../tracker-architecture.md).

## To fill in

- **Pick a tier**: Warden (gRPC daemon in your container — gets dispatched work,
  reports live status, can ask the owner) vs Poller (~30 lines of HTTP polling —
  final result only).
- **What the repo adds** for the Warden tier: the `infra/wardenkit` dependency —
  the only thing another repo imports from Kaizen — plus the compose wiring to
  reach the Hub.
- **Registration + token** — how a project is registered and where its token
  lives (never in the repo).
- **How to verify the join worked** — what the tracker dashboard should show.
- **Working example**: `modules/tracker/example/dummy-project/` speaks the whole
  Warden contract and is the reference implementation to copy.

## Open

- Tracker v2 contracts are still moving; do not write this file as a stable API
  reference until they settle.
