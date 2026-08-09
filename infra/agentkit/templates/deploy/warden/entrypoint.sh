#!/bin/sh
# =============================================================================
# {{PROJECT}} Warden entrypoint — deploy/warden/entrypoint.sh
# =============================================================================
# WHAT: Wires git and `gh` credentials once at boot, then hands off to the
#       Warden — with GH_TOKEN removed from the environment it hands off to.
#
# WHY the token is moved OUT of the environment: `gh` was deliberately left to
#       authenticate from GH_TOKEN, which meant any agent with Bash could run
#       `printenv GH_TOKEN` and read the credential. Storing it in gh's own
#       config file and then dropping the variable closes that, and `git push`
#       keeps working from the credential store either way.
#
# WHY `set -e` is NOT used around the gh login: a failure there must not kill
#       this script. Under `restart: unless-stopped` a hard exit becomes a
#       restart loop, which reads as a broken image and sends whoever is
#       debugging it a long way from the real, minor problem.
# =============================================================================
set -e

# Refuse LOUDLY if $HOME (the state volume) is not writable by this uid. A
# fresh named volume mounts root-owned, and without this probe the very next
# line dies on "could not lock config file /state/.gitconfig", set -e exits,
# and `restart: unless-stopped` turns one missing chown into a silent crash
# loop that reads as a broken image. `make up`, run from deploy/warden/, does
# the chown automatically; this message exists for whoever started the
# container any other way.
if ! touch "$HOME/.writable-probe" 2>/dev/null; then
    echo "FATAL: $HOME is not writable by uid $(id -u)." >&2
    echo "The state volume is created root-owned. One-time fix:" >&2
    echo "  docker run --rm -v {{PROJECT}}-warden-state:/state alpine chown -R $(id -u):$(id -g) /state" >&2
    echo "(deploy/warden/'s 'make up' does this for you.)" >&2
    exit 1
fi
rm -f "$HOME/.writable-probe"

# /repo is a bind mount owned by another uid; without this every git command
# fails as "dubious ownership", which reads like a fleet bug.
git config --global --add safe.directory /repo
git config --global user.name  "${GIT_USER_NAME:-{{PROJECT}} fleet}"
git config --global user.email "${GIT_USER_EMAIL:-fleet@{{PROJECT}}.invalid}"

if [ -n "$GH_TOKEN" ]; then
    git config --global credential.helper store
    printf 'https://oauth2:%s@github.com\n' "$GH_TOKEN" > "$HOME/.git-credentials"
    chmod 600 "$HOME/.git-credentials"

    # `env -u` is REQUIRED, not cosmetic: gh refuses to store a token while
    # GH_TOKEN is set in the environment, exits non-zero, and — with `set -e` —
    # would take the entrypoint down with it. The `|| echo` keeps a failure
    # here advisory: git push still works from the credential store above.
    if printf '%s' "$GH_TOKEN" | env -u GH_TOKEN gh auth login --with-token 2>/dev/null; then
        echo "gh: authenticated from its own config (not from the environment)"
    else
        echo "gh: could not store the token — push still works via git credentials" >&2
    fi
else
    echo "GH_TOKEN is empty — this fleet is LOCAL-ONLY: it cannot push or open PRs." >&2
    echo "  If you did set it in deploy/warden/.env, the usual cause is compose" >&2
    echo "  invoked from the wrong directory (e.g. the repo root): --env-file is" >&2
    echo "  resolved relative to where 'docker compose'/'make up' runs, so from" >&2
    echo "  anywhere but deploy/warden/ it reads the wrong .env — or none — and" >&2
    echo "  every \${VAR:-} silently becomes empty." >&2
fi

# Hand off WITHOUT the token. The Warden never needs the variable — gh reads its
# config, git reads the credential store — and every agent it spawns inherits
# this environment.
#
# Still open, and worth knowing rather than pretending otherwise:
# ~/.git-credentials is plaintext and `cat` still reads it. Closing that needs a
# credential helper that does not store plaintext.
exec env -u GH_TOKEN "$@"
