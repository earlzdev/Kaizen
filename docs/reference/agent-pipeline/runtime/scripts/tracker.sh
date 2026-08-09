#!/usr/bin/env bash
# Tracker CLI — agents use this to read/update tasks and commands.
#
# Usage:
#   scripts/tracker.sh task:next                          → print next pending task as JSON
#   scripts/tracker.sh task:get <id>                      → get task by id
#   scripts/tracker.sh task:list [status]                 → list all tasks (optional filter by status)
#   scripts/tracker.sh task:count [status]                → count tasks (optional filter by status)
#   scripts/tracker.sh task:create <title> <desc> <type> <notes>  → create new task
#   scripts/tracker.sh task:status <id> <status>          → update status
#   scripts/tracker.sh task:pr <id> <pr_url>              → set PR url
#   scripts/tracker.sh task:notes <id> <notes>            → append notes
#   scripts/tracker.sh cmd:next                           → get next pending command
#   scripts/tracker.sh cmd:ack <id> [done|processing]    → acknowledge command

set -euo pipefail

TRACKER_URL="${TRACKER_URL:-http://localhost:3333}"
ACTION="${1:-}"

require_arg() {
  if [[ -z "${2:-}" ]]; then
    echo "Error: $1 requires argument" >&2
    exit 1
  fi
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'
}

case "$ACTION" in

  task:next)
    curl -sf "${TRACKER_URL}/api/tasks/next"
    ;;

  task:list)
    STATUS="${2:-}"
    if [[ -n "$STATUS" ]]; then
      curl -sf "${TRACKER_URL}/api/tasks?status=${STATUS}"
    else
      curl -sf "${TRACKER_URL}/api/tasks"
    fi
    ;;

  task:count)
    STATUS="${2:-}"
    if [[ -n "$STATUS" ]]; then
      curl -sf "${TRACKER_URL}/api/tasks?status=${STATUS}" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'
    else
      curl -sf "${TRACKER_URL}/api/tasks" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'
    fi
    ;;

  task:create)
    require_arg task:create "${2:-}"
    TITLE=$(printf '%s' "${2}" | json_escape)
    DESC=$(printf '%s' "${3:-}" | json_escape)
    TYPE="${4:-feature}"
    NOTES=$(printf '%s' "${5:-}" | json_escape)
    curl -sf -X POST "${TRACKER_URL}/api/tasks" \
      -H "Content-Type: application/json" \
      -d "{\"title\": ${TITLE}, \"description\": ${DESC}, \"type\": \"${TYPE}\", \"notes\": ${NOTES}}"
    ;;

  task:get)
    require_arg task:get "${2:-}"
    curl -sf "${TRACKER_URL}/api/tasks/${2}"
    ;;

  task:status)
    require_arg task:status "${2:-}"
    require_arg status "${3:-}"
    curl -sf -X PUT "${TRACKER_URL}/api/tasks/${2}" \
      -H "Content-Type: application/json" \
      -d "{\"status\": \"${3}\"}"
    ;;

  task:pr)
    require_arg task:pr "${2:-}"
    require_arg pr_url "${3:-}"
    curl -sf -X PUT "${TRACKER_URL}/api/tasks/${2}" \
      -H "Content-Type: application/json" \
      -d "{\"pr_url\": \"${3}\"}"
    ;;

  task:branch)
    require_arg task:branch "${2:-}"
    require_arg branch "${3:-}"
    curl -sf -X PUT "${TRACKER_URL}/api/tasks/${2}" \
      -H "Content-Type: application/json" \
      -d "{\"branch\": \"${3}\"}"
    ;;

  task:notes)
    require_arg task:notes "${2:-}"
    require_arg notes "${3:-}"
    ESCAPED=$(echo "${3}" | json_escape)
    curl -sf -X PUT "${TRACKER_URL}/api/tasks/${2}" \
      -H "Content-Type: application/json" \
      -d "{\"notes\": ${ESCAPED}}"
    ;;

  cmd:next)
    curl -sf "${TRACKER_URL}/api/commands/next"
    ;;

  cmd:ack)
    require_arg cmd:ack "${2:-}"
    STATUS="${3:-done}"
    curl -sf -X PUT "${TRACKER_URL}/api/commands/${2}/ack" \
      -H "Content-Type: application/json" \
      -d "{\"status\": \"${STATUS}\"}"
    ;;

  *)
    echo "Unknown action: $ACTION" >&2
    echo ""
    echo "Available actions:"
    echo "  task:next                     Get next pending task"
    echo "  task:get <id>                 Get task by id"
    echo "  task:list [status]            List all tasks (optional status filter)"
    echo "  task:count [status]           Count tasks (optional status filter)"
    echo "  task:create <title> <desc> <type> <notes>  Create new task"
    echo "  task:status <id> <status>     Update task status"
    echo "  task:pr <id> <url>            Set PR url"
    echo "  task:branch <id> <branch>     Set branch name"
    echo "  task:notes <id> <text>        Update notes"
    echo "  cmd:next                      Get next pending command"
    echo "  cmd:ack <id> [status]         Acknowledge command"
    exit 1
    ;;
esac
