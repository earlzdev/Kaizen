#!/usr/bin/env bash
# ============================================================================
# gRPC codegen — proto/gen.sh
# ----------------------------------------------------------------------------
# WHAT: Compiles every *.proto in this directory into Python (message classes +
#       gRPC stubs) under proto/gen/.
#
# WHY a script and not codegen-at-runtime: the plan (Phase 0/1) keeps ONE set
#   of .proto files as the single source of truth and generates code from them
#   for both Brain and modules. Committing the generated code (this script just
#   regenerates it) means containers don't need protoc at build time and every
#   service imports the exact same wire types.
#
# WHY the sed fixup: protoc emits `import health_pb2` (a bare top-level import)
#   inside health_pb2_grpc.py. That only resolves if proto/gen is on sys.path as
#   a top-level dir. We import it as a package (`proto.gen.health_pb2`), so we
#   rewrite that line to a package-relative import. This is the standard,
#   well-known grpcio-tools papercut.
#
# HOW to run: `bash proto/gen.sh` (or `make proto`). Re-run after editing any
#   .proto, then commit proto/gen/.
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${HERE}/gen"
mkdir -p "${OUT}"

# Make generated modules importable as a package (proto.gen.*).
touch "${OUT}/__init__.py"

python -m grpc_tools.protoc \
  --proto_path="${HERE}" \
  --python_out="${OUT}" \
  --grpc_python_out="${OUT}" \
  "${HERE}"/*.proto

# Rewrite bare cross-file imports in the *_grpc.py stubs to package-relative
# ones so `from proto.gen import ...` works without polluting sys.path.
for f in "${OUT}"/*_pb2_grpc.py; do
  [ -e "$f" ] || continue
  # macOS/BSD sed needs the empty '' after -i; GNU sed tolerates it via a shim.
  sed -i.bak -E 's/^import ([a-zA-Z0-9_]+)_pb2 as/from . import \1_pb2 as/' "$f"
  rm -f "${f}.bak"
done

echo "Generated gRPC code in ${OUT}"
