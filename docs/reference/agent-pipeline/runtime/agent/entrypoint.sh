#!/usr/bin/env bash
set -e

REPO_URL="${REPO_URL:-https://github.com/your-org/your-repo.git}"

# ── Auth: ~/.claude.json setup ────────────────────────────────────────────────
# Host file is bind-mounted read-only at ~/.claude.json.host
# We copy it so each container has its own writable copy (avoids corruption).
HOST_AUTH="${HOME}/.claude.json.host"
VOLUME_AUTH="${HOME}/.claude/.claude.json"

if [ -f "${HOST_AUTH}" ] && [ -s "${HOST_AUTH}" ]; then
  cp "${HOST_AUTH}" "${HOME}/.claude.json"
  echo "[entrypoint] Auth: copied ~/.claude.json from host (own writable copy)"
elif [ -d "${HOST_AUTH}" ]; then
  # Docker created a directory instead of a file (host file didn't exist at mount time)
  echo "[entrypoint] Auth: ~/.claude.json.host is a directory (ignored) — using volume"
else
  # No host file — fall back to symlink into named volume
  touch "${VOLUME_AUTH}" 2>/dev/null || true
  if [ ! -L "${HOME}/.claude.json" ]; then
    rm -f "${HOME}/.claude.json"
    ln -sf "${VOLUME_AUTH}" "${HOME}/.claude.json" \
      && echo "[entrypoint] Auth: ~/.claude.json linked to volume" \
      || echo "[entrypoint] WARNING: could not create auth symlink"
  fi
  if [ ! -s "${VOLUME_AUTH}" ]; then
    echo "[entrypoint] WARNING: No auth tokens in volume — run 'make login' or 'make import-auth'"
  fi
fi

# ── Git: configure PAT authentication ────────────────────────────────────────
if [ -n "${GH_TOKEN:-}" ]; then
  git config --global credential.helper store
  echo "https://oauth2:${GH_TOKEN}@github.com" > "${HOME}/.git-credentials"
  git config --global url."https://oauth2:${GH_TOKEN}@github.com/".insteadOf "https://github.com/"
  echo "[entrypoint] GitHub token configured"
else
  echo "[entrypoint] WARNING: GH_TOKEN not set — git push will fail"
fi

git config --global user.email "${GIT_USER_EMAIL:-agent@yourproject.dev}"
git config --global user.name  "${GIT_USER_NAME:-YourProject Agent}"
git config --global safe.directory "*"
git config --global init.defaultBranch main

# ── Clone repo on first start (empty volume) ─────────────────────────────────
# Use a lockfile so parallel containers (agent + agent-runner) don't clone simultaneously.
CLONE_LOCK=/tmp/.workspace-clone.lock
if [ ! -f /workspace/.git/config ]; then
  # Acquire lock — if another container is already cloning, wait for it to finish
  if mkdir "${CLONE_LOCK}" 2>/dev/null; then
    echo "[entrypoint] Workspace is empty — cloning ${REPO_URL}..."
    git clone "${REPO_URL}" /workspace
    rmdir "${CLONE_LOCK}"
    echo "[entrypoint] Clone complete"
  else
    echo "[entrypoint] Another container is cloning — waiting..."
    while [ -d "${CLONE_LOCK}" ] || [ ! -f /workspace/.git/config ]; do sleep 2; done
    echo "[entrypoint] Workspace ready (cloned by another container)"
  fi
else
  echo "[entrypoint] Workspace already initialized"
  cd /workspace && git pull origin main 2>/dev/null || true
fi

# ── Mobile: write local build config ─────────────────────────────────────────
# (example targets a Gradle-based mobile toolchain's local.properties / SDK
# path convention — swap this block for whatever your own mobile build needs)
if [ -n "${GPR_USER:-}" ] && [ -n "${GPR_TOKEN:-}" ]; then
  cat > /workspace/mobile/local.properties <<EOF
sdk.dir=${ANDROID_HOME}
gpr.user=${GPR_USER}
gpr.token=${GPR_TOKEN}
EOF
  echo "[entrypoint] local.properties written"
else
  # At minimum set sdk.dir so the mobile build tool finds the SDK
  if [ -d /workspace/mobile ]; then
    echo "sdk.dir=${ANDROID_HOME}" > /workspace/mobile/local.properties
    echo "[entrypoint] local.properties written (sdk.dir only, GPR_USER/GPR_TOKEN not set)"
  fi
fi

# ── gh CLI: auth via token ────────────────────────────────────────────────────
if [ -n "${GH_TOKEN:-}" ]; then
  echo "${GH_TOKEN}" | gh auth login --with-token 2>/dev/null || true
fi

echo "[entrypoint] Ready — $(cd /workspace && git log --oneline -1 2>/dev/null || echo 'no commits')"
echo ""

exec "$@"
