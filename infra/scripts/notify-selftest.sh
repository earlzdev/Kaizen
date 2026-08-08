#!/usr/bin/env bash
# =============================================================================
# Notification self-test — infra/scripts/notify-selftest.sh
# =============================================================================
# WHAT: Answers the one question a log cannot: "is anything actually reaching
#       me?" It sends a real event down the real path — module → Brain POST
#       /event → the owner's agent → Telegram.
#
# WHY it exists: MODULE_EVENT_TOKEN being unset drops every report, question and
#       requeue, and the only trace is a line in a container log nobody is
#       reading. The first real project lost five reports that way, two of them
#       finished answers. Boot-time checks now catch an EMPTY or TEMPLATE token,
#       but they cannot catch the other half — the two sides holding DIFFERENT
#       values, which authenticates as a 401 at runtime and looks like silence.
#
# WHY it never prints the token: this is the one script whose whole job is to
#       handle it, and a self-test that echoes a secret into a terminal is worse
#       than the outage it diagnoses.
#
# HOW:  make notify-selftest      (or: bash infra/scripts/notify-selftest.sh)
#       Expect a message in Telegram within a few seconds.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/../.."

if [[ ! -f .env ]]; then
    echo "No .env in $(pwd) — nothing to test." >&2
    exit 1
fi

# Read ONLY the two keys we need, without sourcing the file: sourcing .env runs
# whatever is in it and exports every secret into this shell.
token="$(grep -E '^MODULE_EVENT_TOKEN=' .env | head -1 | cut -d= -f2- || true)"
port="$(grep -E '^BRAIN_PORT=' .env | head -1 | cut -d= -f2- || true)"
# 8772 is Brain's real default (brain/config.py) and what the dev overlay
# publishes; .env.example ships BRAIN_PORT commented out, so this fallback is
# what actually gets used.
port="${port:-8772}"

if [[ -z "$token" ]]; then
    echo "MODULE_EVENT_TOKEN is not set in .env." >&2
    echo "Reports and questions cannot reach you at all. Set it (the SAME value" >&2
    echo "is read by Brain and by the tracker) and try again." >&2
    exit 1
fi
# tr, not `${token,,}`: that expansion is bash 4, and macOS ships bash 3.2 —
# where it is a fatal "bad substitution" under set -e, killing the self-test
# for every NON-placeholder token too.
lower="$(printf '%s' "$token" | tr '[:upper:]' '[:lower:]')"
case "$lower" in
    change-me*|replace-me*|your-*|todo*)
        echo "MODULE_EVENT_TOKEN is still the template value from .env.example." >&2
        echo "Generate a real one, e.g.:  openssl rand -hex 32" >&2
        exit 1
        ;;
esac

echo "Sending a test event through Brain on :$port …"
code="$(curl -s -o /tmp/notify-selftest.out -w '%{http_code}' \
    -X POST "http://127.0.0.1:${port}/event" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -d '{"kind":"selftest","text":"✅ Connectivity check: notifications are getting through."}' || echo 000)"

case "$code" in
    200|202)
        echo "Brain accepted it. Check Telegram — the message should be there."
        echo "If it is NOT, the break is between Brain and the agent: the agent"
        echo "has not enrolled, or DELIVERY_TOKEN differs between the two."
        ;;
    401)
        echo "401 — Brain rejected the token." >&2
        echo "Brain and this .env disagree about MODULE_EVENT_TOKEN. This is the" >&2
        echo "failure the boot checks cannot see: both sides are SET, and they" >&2
        echo "are not the same value. Fix .env, then: make down && make up" >&2
        exit 1
        ;;
    503)
        echo "503 — Brain has the token but no reachable agent:" >&2
        cat /tmp/notify-selftest.out >&2; echo >&2
        echo "Usually means the agent has not enrolled yet: make approve" >&2
        exit 1
        ;;
    000)
        echo "Could not reach Brain on 127.0.0.1:${port}." >&2
        echo "Is the stack up (make ps), and does the dev overlay publish the port?" >&2
        exit 1
        ;;
    *)
        echo "Unexpected HTTP $code:" >&2
        cat /tmp/notify-selftest.out >&2; echo >&2
        exit 1
        ;;
esac
