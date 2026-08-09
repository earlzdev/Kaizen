# =============================================================================
# Dummy project image — modules/tracker/example/dummy-project/Dockerfile
# =============================================================================
# WHAT: The smallest image that can be a tracker v2 project. python:3.12-slim,
#       grpcio, `infra/` copied in, and one warden.py.
#
# WHY it is this small, and why that matters: it is the honest measure of what
#       integrating with the tracker costs. A project brings its own toolchain
#       (Go, Rust, Node, whatever it actually builds with) and adds THIS on top
#       — two Python packages and one file. If this Dockerfile needed pydantic,
#       SQLAlchemy or aiohttp, every project would inherit them.
#
# WHY the build context is the KAIZEN REPO ROOT: `infra/wardenkit` and the
#       generated `infra/proto/gen` are what a project imports, and there is no
#       package on an index to install them from yet. A real external project
#       would vendor or pip-install them; copying from the repo keeps this
#       example honest about exactly which files are needed.
# =============================================================================

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# The kit's entire runtime dependency set: grpcio for the transport, protobuf
# because the COMMITTED generated stubs (infra/proto/gen/*_pb2.py) import it.
# grpcio does not pull protobuf in on its own — only grpcio-TOOLS does, and
# that is a codegen-time dependency Kaizen keeps to itself. Nothing else:
# infra/wardenkit deliberately hand-rolls its flat-YAML reader rather than add
# PyYAML, precisely so this list stays short. Floors match Kaizen's
# requirements.txt, since both sides must agree on the wire format.
RUN pip install --no-cache-dir "grpcio>=1.83,<2" "protobuf>=7.35,<8"

# Everything a project needs from Kaizen: the generated contract and the kit.
# infra/quota.py: clirunner.py imports it at module level (spent-subscription
# detection) even though a ClaudeRunner is never constructed in this dummy
# project — the import still runs.
COPY infra/__init__.py                infra/__init__.py
COPY infra/quota.py                   infra/quota.py
COPY infra/proto/__init__.py          infra/proto/__init__.py
COPY infra/proto/gen                  infra/proto/gen
COPY infra/wardenkit                  infra/wardenkit

# ...and the project's own half.
COPY modules/tracker/example/dummy-project/warden.py warden.py

CMD ["python", "warden.py"]
