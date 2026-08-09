# =============================================================================
# {{PROJECT}} Warden image — deploy/warden/Dockerfile
# =============================================================================
# WHAT: The runtime the agent fleet works in: python + the `claude` CLI + git +
#       gh, and nothing about this project's own stack.
#
# WHY the Warden source is NOT copied in as the thing that runs: the compose
#       file starts `/repo/deploy/warden/warden.py` from the mount, so a fix is
#       `git pull && docker restart` instead of a rebuild sequenced against
#       whatever Directive is running. The copy below is kept deliberately as
#       the documented fallback (`command: ["python", "/app/warden.py"]`) for a
#       host where the repo and the container do not share a filesystem.
#
# WHY the vendored kit is committed under deploy/warden/vendor/: this project
#       imports `infra.wardenkit` from Kaizen, and there is no package on an
#       index to install it from. A DEPLOY HOST NEEDS NOTHING — the copy is in
#       the repo. Re-vendoring is a development step (`make vendor-kit`), not a
#       deployment one.
# =============================================================================

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/vendor

WORKDIR /app

# git + gh for the fleet's own workflow; nodejs for the `claude` CLI; ca-certs
# because everything above talks TLS.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*
{{DIND_DOCKERFILE_BLOCK}}

RUN npm install -g @anthropic-ai/claude-code

# The kit's entire runtime dependency set: grpcio for the transport, protobuf
# because the committed generated stubs import it.
RUN pip install --no-cache-dir "grpcio>=1.83,<2" "protobuf>=7.35,<8"

# The fallback copy — see the header. `vendor/` carries infra.wardenkit and the
# generated proto stubs; both are committed in this repo.
COPY vendor /app/vendor
COPY warden.py /app/warden.py

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "/app/warden.py"]
