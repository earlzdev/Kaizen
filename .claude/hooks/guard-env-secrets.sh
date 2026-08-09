#!/usr/bin/env bash
# PreToolUse hook on Bash: blocks commands that would print real .env secrets
# into the transcript. Two patterns:
#   a) file readers/editors (cat, tail, grep, ...) opened directly on .env
#   b) `docker compose ... config|convert` while a real .env sits in cwd —
#      env_file: .env in compose.yml resolves against the real file
#      regardless of --env-file, so that flag is NOT a safe exception here.
set -eu

input="$(cat)"
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty')"

if [ -z "$cmd" ]; then
  exit 0
fi

deny() {
  reason="$1"
  jq -n --arg reason "$reason" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
  exit 0
}

# Tokenize: swap shell metacharacters/quotes for spaces, then split on
# whitespace via a here-string (never process substitution — a command
# substitution with no trailing newline can make `read` return non-zero
# and, under `set -e`, silently kill the script before the checks run).
normalized="$(printf '%s' "$cmd" | tr '|;&()"'\''' ' ')"
read -ra tokens <<< "$normalized"

is_env_path() {
  [ "$1" = ".env" ] && return 0
  case "$1" in
    */.env) return 0 ;;
  esac
  return 1
}

banned_reader=""
env_arg=""
has_compose=""
has_config_verb=""

for tok in "${tokens[@]}"; do
  case "$tok" in
    cat|less|more|head|tail|bat|strings|nano|vim|grep|awk|sed)
      banned_reader="$tok" ;;
  esac
  if is_env_path "$tok"; then
    env_arg="$tok"
  fi
  if [ "$tok" = "compose" ]; then
    has_compose=1
  fi
  if [ "$tok" = "config" ] || [ "$tok" = "convert" ]; then
    has_config_verb=1
  fi
done

if [ -n "$banned_reader" ] && [ -n "$env_arg" ]; then
  deny "This command reads the real .env file with '$banned_reader', which would print live secrets into the transcript. Use .env.example instead — it's the safe, committed template."
fi

if [ -n "$has_compose" ] && [ -n "$has_config_verb" ] && [ -e .env ]; then
  deny "docker compose config/convert resolves each service's env_file: .env directive against the real .env in this directory and prints every secret to stdout. --env-file does NOT prevent this — it only affects \${VAR} substitution inside compose.yml itself, not the separate env_file: .env directive, which always resolves against the real file regardless of that flag. Move or rename .env first, or run this where only .env.example exists."
fi

exit 0
