# =============================================================================
# Tracker file conventions — infra/wardenkit/trackerfiles.py
# =============================================================================
# WHAT: The one implementation of the `docs/tracker/{task_id}/` layout every
#       project's agents share:
#           docs/tracker/{task_id}/tasks/{from}-to-{to}-{task_id}.md   (Handoff)
#           docs/tracker/{task_id}/status/{agent_slug}.yml             (Status)
#       Write a Handoff, write/read a Status — and nothing else.
#
# WHY files and not the Hub's database (architecture §1): a Handoff is a local
#       message between two cooperating agents INSIDE one project. As files they
#       are greppable, auditable, survive a container restart, and a human
#       reviewer can read the whole decision trail in the repo where the work
#       happened. The Hub can't read another project's disk anyway, and putting
#       them in Postgres would buy a second, worse copy of that trail.
#
# WHY Status is BOTH a file and a mirrored row: the file is the project's own
#       source of truth (an agent can read its lead's status without a network
#       call); the row pushed to the Hub is observability, so "who is doing what
#       right now" can be answered across every project at once.
#
# WHY a hand-rolled flat-YAML reader/writer instead of PyYAML: this library is
#       the ONLY thing a project imports from Kaizen, so every dependency it
#       carries becomes a dependency of every project that integrates. A Status
#       file is a flat mapping of strings — the subset below covers it fully,
#       in ~60 lines, and keeps wardenkit's install to grpcio alone. (Same
#       reasoning as infra/modkit's hand-rolled validate_args.)
#
# HOW:  files = TrackerFiles(repo_root, task_id)
#       files.write_handoff("architect-xavier", "backend-lead-tesla", body)
#       files.write_status("backend-dev-anderson", state="in_progress", ...)
# =============================================================================

import datetime
import hashlib
import re
from pathlib import Path


def slugify(text: str, *, fallback: str = "task") -> str:
    """A filesystem- and URL-safe id from free text.

    Used to derive a `task_id` from a Directive's title when the owner didn't
    supply one, and to name every Handoff and Status file. Deliberately
    ASCII-only and lowercase: these become directory and file names shared
    between a container, a git repo and a database, and those three disagree
    about non-ASCII filenames.

    WHY a hash suffix when nothing survives: the owner writes in Russian, so
    "архитектор" and "разработчик" would BOTH collapse to the bare fallback and
    silently overwrite each other's status file. Appending a short digest of
    the original text keeps distinct inputs distinct, which is the one property
    a name used as a filesystem key has to have.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = slug[:60].strip("-")
    if slug:
        return slug
    if not text.strip():
        return fallback
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{fallback}-{digest}"


def _dump_scalar(value: str) -> str:
    """Render one value as YAML: a block scalar if multi-line, else quoted.

    Quoting even simple values is deliberate — it means `state: yes` or a
    progress note that starts with `-` can never be re-read as a bool or a list.
    """
    text = "" if value is None else str(value)
    if "\n" in text:
        body = "\n".join("  " + line for line in text.split("\n"))
        return "|\n" + body
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _load_scalar(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return raw


def dumps(data: dict) -> str:
    """Serialize a flat mapping of strings to the YAML subset above."""
    lines = []
    for key, value in data.items():
        lines.append(f"{key}: {_dump_scalar(value)}")
    return "\n".join(lines) + "\n"


def loads(text: str) -> dict:
    """Parse the YAML subset `dumps` writes. Unknown shapes are skipped, not
    raised on: a half-written status file must never crash the agent reading
    it — it just means that agent hasn't said anything usable yet."""
    out: dict[str, str] = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line or line.startswith(" "):
            continue
        key, _, raw = line.partition(":")
        key, raw = key.strip(), raw.strip()
        if raw == "|":
            block: list[str] = []
            while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
                block.append(lines[i][2:])
                i += 1
            # Trailing blank lines belong to the file, not to the value.
            while block and not block[-1].strip():
                block.pop()
            out[key] = "\n".join(block)
        else:
            out[key] = _load_scalar(raw)
    return out


class TrackerFiles:
    """The `docs/tracker/{task_id}/` tree for ONE Directive's work.

    One instance per task_id, which is what keeps two concurrent Directives
    from colliding on disk (architecture §7 case 14): distinct task_ids mean
    distinct trees, so two fleets never write the same Handoff path.
    """

    def __init__(self, repo_root: str | Path, task_id: str) -> None:
        self.repo_root = Path(repo_root)
        self.task_id = task_id

    @property
    def dir(self) -> Path:
        return self.repo_root / "docs" / "tracker" / self.task_id

    @property
    def tasks_dir(self) -> Path:
        return self.dir / "tasks"

    @property
    def status_dir(self) -> Path:
        return self.dir / "status"

    def ensure(self) -> None:
        """Create the tree. Idempotent — called freely before any write."""
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.status_dir.mkdir(parents=True, exist_ok=True)

    # -- Handoffs ----------------------------------------------------------
    def handoff_path(self, from_agent: str, to_agent: str) -> Path:
        return self.tasks_dir / f"{slugify(from_agent)}-to-{slugify(to_agent)}-{self.task_id}.md"

    def write_handoff(self, from_agent: str, to_agent: str, body: str) -> Path:
        """Write one agent's handoff to another. Returns the path.

        Overwrites: a Handoff is the CURRENT instruction from one agent to the
        next, and the history that matters is in git, which is watching this
        file. Appending would produce a file whose top half is stale advice the
        receiving agent still reads.
        """
        self.ensure()
        path = self.handoff_path(from_agent, to_agent)
        header = (
            f"# {from_agent} → {to_agent}\n\n"
            f"- task: `{self.task_id}`\n"
            f"- written: {_now_iso()}\n\n---\n\n"
        )
        path.write_text(header + body.rstrip() + "\n", encoding="utf-8")
        return path

    def read_handoff(self, from_agent: str, to_agent: str) -> str | None:
        path = self.handoff_path(from_agent, to_agent)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def list_handoffs(self) -> list[Path]:
        if not self.tasks_dir.exists():
            return []
        return sorted(self.tasks_dir.glob("*.md"))

    # -- Status ------------------------------------------------------------
    def status_path(self, agent_slug: str) -> Path:
        return self.status_dir / f"{slugify(agent_slug)}.yml"

    def write_status(
        self,
        agent_slug: str,
        *,
        state: str,
        role: str = "",
        phase: str = "",
        progress: str = "",
        blockers: str = "",
    ) -> Path:
        """Overwrite this agent's Status for this task. Returns the path.

        Overwritten, never appended: Status answers "where is this agent NOW".
        """
        self.ensure()
        path = self.status_path(agent_slug)
        path.write_text(
            dumps(
                {
                    "agent": agent_slug,
                    "role": role,
                    "state": state,
                    "phase": phase,
                    "progress": progress,
                    "blockers": blockers,
                    "updated_at": _now_iso(),
                }
            ),
            encoding="utf-8",
        )
        return path

    def read_status(self, agent_slug: str) -> dict | None:
        path = self.status_path(agent_slug)
        if not path.exists():
            return None
        return loads(path.read_text(encoding="utf-8"))

    def read_all_statuses(self) -> dict[str, dict]:
        """Every agent's latest Status for this task, keyed by agent slug.

        WHY the FILENAME is the key and the `agent:` field is not: the filename
        is what `status_path()` derives from the slug, so it is the one value
        guaranteed to be the slug the roster declared — and the roster slug is
        what the Hub joins live status onto. A fleet of Claude sub-agents writes
        these files by hand, and a persona that writes `agent: anderson` inside
        `dev-anderson.yml` would otherwise mirror its state onto a member that
        does not exist, leaving the real one showing `idle` while it works.

        `state` is also accepted as `status`, for the same reason: a hand-written
        file is going to use whichever word the persona's example showed, and
        silently ignoring one of them is the same invisible failure.
        """
        if not self.status_dir.exists():
            return {}
        out: dict[str, dict] = {}
        for path in sorted(self.status_dir.glob("*.yml")):
            data = loads(path.read_text(encoding="utf-8"))
            if not data.get("state") and data.get("status"):
                data["state"] = data["status"]
            if not data.get("updated_at") and data.get("updated"):
                data["updated_at"] = data["updated"]
            data.setdefault("agent", path.stem)
            out[path.stem] = data
        return out


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


__all__ = ["TrackerFiles", "dumps", "loads", "slugify"]
