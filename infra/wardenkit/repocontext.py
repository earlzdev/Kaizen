# =============================================================================
# Repo context — infra/wardenkit/repocontext.py
# =============================================================================
# WHAT: `build_context(repo_root)` — a short, cheap slice of a project's own
#       durable memory (docs/decisions.md, the tail of CHANGELOG.md, recent
#       git history), assembled fresh for one prompt.
#
# WHY no new persistent store for a live conversation's memory: the project's
#       repo already IS the fleet's long-term memory (docs/decisions.md is
#       "the thing next run is obliged to know" by convention — see every
#       rendered warden.py's `run_ask`). A conversation forgetting things
#       between turns is not a missing database, it is a missing RETRIEVAL
#       step — the Warden never pulled the relevant slice back in. This
#       function is that step, not a new place to write things down.
#
# WHY no embeddings/RAG here: that machinery already exists (the mentor
#       module) for a different job — searching a large, growing knowledge
#       base. `docs/decisions.md` and a CHANGELOG tail are small enough to
#       read in full every turn; adding a vector index to bisect a file that
#       is a few KB would be solving a problem this file doesn't have.
#
# HOW: called once per turn from infra/wardenkit/conversemode.py, concatenated
#       ahead of the conversation history in the prompt.
# =============================================================================

import subprocess
from pathlib import Path

_DECISIONS_PATH = "docs/decisions.md"
_CHANGELOG_PATH = "CHANGELOG.md"


def _tail_changelog(repo_root: Path, entries: int) -> str:
    """The last `entries` `## `-headed sections of a changelog, oldest of the
    kept ones first. A changelog with no `## ` headings is returned whole —
    better to hand over slightly too much than silently nothing."""
    path = repo_root / _CHANGELOG_PATH
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    sections = text.split("\n## ")
    if len(sections) <= 1:
        return text.strip()
    head, rest = sections[0], sections[1:]
    kept = rest[-entries:]
    return (head.strip() + "\n\n## " + "\n## ".join(kept)).strip()


def _recent_commits(repo_root: Path, count: int) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--oneline", f"-{count}"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def build_context(
    repo_root: str, *, changelog_entries: int = 10, log_entries: int = 20
) -> str:
    """A prompt-ready section of repo context, or "" when the repo has none of
    these (a fresh checkout, a project with no CHANGELOG yet) — so a caller
    can concatenate the result unconditionally, same contract as
    ConversationLog.render()."""
    root = Path(repo_root)
    parts = []

    decisions_path = root / _DECISIONS_PATH
    if decisions_path.exists():
        try:
            decisions = decisions_path.read_text(encoding="utf-8").strip()
        except OSError:
            decisions = ""
        if decisions:
            parts.append("--- docs/decisions.md ---\n" + decisions)

    changelog = _tail_changelog(root, changelog_entries)
    if changelog:
        parts.append(f"--- CHANGELOG.md (last {changelog_entries} entries) ---\n" + changelog)

    commits = _recent_commits(root, log_entries)
    if commits:
        parts.append(f"--- git log --oneline -{log_entries} ---\n" + commits)

    if not parts:
        return ""
    return "=== Контекст репозитория ===\n" + "\n\n".join(parts)


__all__ = ["build_context"]
