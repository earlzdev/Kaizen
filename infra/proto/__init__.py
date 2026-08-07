# =============================================================================
# Proto — proto/
# =============================================================================
# WHAT: The single source of truth for v2 gRPC contracts. Holds the `.proto`
#       files and, under proto/gen/, the committed generated Python (message
#       classes + gRPC stubs) that both Brain and modules import.
#
# WHY one shared location: the plan (docs/plans/kaizen-v2-rollout.md) freezes the
#       contracts first and generates code for every service from the same
#       definitions, so the wire types can never drift between Brain and modules.
#       Generated code is committed so containers need no protoc at build time.
#
# HOW: edit a `.proto`, re-run `bash proto/gen.sh`, commit proto/gen/. Import the
#       result as `from infra.proto.gen import health_pb2, health_pb2_grpc`.
# =============================================================================
