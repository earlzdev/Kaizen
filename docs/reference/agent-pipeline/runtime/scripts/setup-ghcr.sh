#!/bin/bash
# One-time setup: authenticate Docker daemon with GHCR on this VPS.
#
# Prerequisites:
#   - A GitHub Personal Access Token (classic) with read:packages scope
#   - Docker installed and running
#
# Usage:
#   GHCR_TOKEN=ghp_xxxx ./setup-ghcr.sh

set -euo pipefail

if [ -z "${GHCR_TOKEN:-}" ]; then
  echo "ERROR: GHCR_TOKEN environment variable is required."
  echo "Create a GitHub PAT (classic) with 'read:packages' scope at:"
  echo "  https://github.com/settings/tokens/new"
  exit 1
fi

# GHCR_USER: your GitHub username or org that owns the container registry packages.
GHCR_USER="${GHCR_USER:-your-github-username}"

echo "Logging into ghcr.io..."
echo "${GHCR_TOKEN}" | docker login ghcr.io -u "${GHCR_USER}" --password-stdin

echo ""
echo "Docker is now authenticated with GHCR."
echo "Credentials are stored in ~/.docker/config.json"
echo ""
echo "Next steps:"
echo "  1. Add GHCR_TOKEN to your backend env file (e.g. /home/<user>/backend.env):"
echo "     echo 'GHCR_TOKEN=${GHCR_TOKEN}' >> /home/<user>/backend.env"
echo "  2. Check Docker socket GID:"
echo "     stat -c '%g' /var/run/docker.sock"
echo "  3. Add DOCKER_GID to backend.env if needed (default 999):"
echo "     echo 'DOCKER_GID=<gid>' >> /home/<user>/backend.env"
echo "  4. Restart the service(s) that need the new env vars (e.g. your admin/deploy service)."
