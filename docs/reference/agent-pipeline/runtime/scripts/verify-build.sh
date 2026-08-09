#!/usr/bin/env bash
# Build verification script.
# NOTE: the commands below (a compiled-language build, a mobile build tool,
# a JS/TS build tool) are just an EXAMPLE stack. Swap each function's
# internals for whatever your own backend/mobile/frontend toolchains use —
# nothing about the target names (backend/mobile/frontend/all) is specific
# to any one stack.
# Usage:
#   scripts/verify-build.sh backend          → compile all backend services
#   scripts/verify-build.sh mobile           → run the mobile debug build
#   scripts/verify-build.sh frontend         → frontend type-check + build
#   scripts/verify-build.sh all              → backend + mobile + frontend
#   scripts/verify-build.sh backend <svc>    → single service (e.g. service-a — see build_backend() below)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TARGET="${1:-all}"
SERVICE="${2:-}"

FAILED=()

build_backend_service() {
  local svc="$1"
  local dir="${ROOT}/backend/${svc}"
  if [[ ! -d "$dir" ]]; then
    echo "  ⚠️  Directory not found: $dir"
    return
  fi
  if [[ ! -f "${dir}/go.mod" ]] && [[ ! -f "${ROOT}/backend/go.mod" ]]; then
    echo "  ⚠️  No build manifest in ${svc}, skipping"
    return
  fi
  echo "  → building ${svc}..."
  # This compiles the same code that the Dockerfile would compile.
  # A clean local build guarantees the Docker image will also build successfully.
  # (The `go.mod` / `go build` calls here are this example's stand-in for
  # "your compiled backend language's build command" — swap for your own.)
  local mod_root="${ROOT}/backend"
  if [[ -f "${dir}/go.mod" ]]; then
    mod_root="${dir}"
  fi
  if (cd "${dir}/cmd/app" 2>/dev/null || cd "${dir}" && go build ./... 2>&1); then
    echo "  ✓ ${svc}"
  else
    echo "  ✗ ${svc} FAILED"
    FAILED+=("$svc")
  fi
}

build_backend() {
  echo "▶ Backend build verification"
  if [[ -n "$SERVICE" ]]; then
    build_backend_service "$SERVICE"
  else
    # List your own backend services here (one directory under backend/ per service).
    # Example placeholder — replace with your project's actual service names:
    for svc in your_service; do
      build_backend_service "$svc"
    done
  fi
}

build_mobile() {
  # Example uses a Gradle-based mobile build; swap for your own mobile
  # toolchain's debug-build command if it differs.
  echo "▶ Mobile build verification (debug build)"
  local mobile_dir="${ROOT}/mobile"
  if [[ ! -d "$mobile_dir" ]]; then
    echo "  ⚠️  mobile directory not found"
    return
  fi
  pushd "$mobile_dir" > /dev/null
  if ./gradlew :androidApp:assembleDebug --quiet 2>&1 | tail -5; then
    echo "  ✓ mobile debug build"
  else
    echo "  ✗ mobile debug build FAILED"
    FAILED+=("mobile")
  fi
  popd > /dev/null
}

build_frontend() {
  # Example uses a Node/TS toolchain (tsc + vite); swap for your own
  # frontend build/type-check commands if they differ.
  echo "▶ Frontend build verification"
  local frontend_dir="${ROOT}/frontend"
  if [[ ! -d "$frontend_dir" ]]; then
    echo "  ⚠️  frontend directory not found, skipping"
    return
  fi
  if [[ ! -f "${frontend_dir}/package.json" ]]; then
    echo "  ⚠️  no package.json in frontend, skipping"
    return
  fi
  pushd "$frontend_dir" > /dev/null
  if npx tsc --noEmit 2>&1 | tail -10; then
    echo "  ✓ TypeScript type-check"
  else
    echo "  ✗ TypeScript type-check FAILED"
    FAILED+=("frontend-typecheck")
  fi
  if npx vite build 2>&1 | tail -5; then
    echo "  ✓ Vite build"
  else
    echo "  ✗ Vite build FAILED"
    FAILED+=("frontend-build")
  fi
  popd > /dev/null
}

case "$TARGET" in
  backend)  build_backend ;;
  mobile)   build_mobile  ;;
  frontend) build_frontend ;;
  all)      build_backend; build_mobile; build_frontend ;;
  *)
    echo "Usage: $0 [backend|mobile|frontend|all] [service_name]"
    exit 1
    ;;
esac

echo ""
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "✅ All builds passed"
  exit 0
else
  echo "❌ Failed: ${FAILED[*]}"
  exit 1
fi
