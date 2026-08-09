#!/usr/bin/env python3
"""
Agent Runner — HTTP trigger server that spawns Claude on demand.

POST /trigger   receive command from tracker, spawn Claude if idle
POST /clear     kill current session, reset context (next run won't use --continue)
GET  /status    {"busy": bool}
GET  /health    {"ok": true}
"""

import glob
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("AGENT_RUNNER_PORT", 3334))
WORKSPACE = "/workspace"
GIT_WORKFLOW_PATH = os.path.join(WORKSPACE, "tools/runtime/git-workflow.md")
COMMANDS_DIR = os.path.join(WORKSPACE, ".claude/commands")

_CLAUDE_CONFIG = os.path.expanduser("~/.claude.json")
_CLAUDE_BACKUPS_DIR = os.path.expanduser("~/.claude/backups")

# ── State ─────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_process: subprocess.Popen | None = None
_use_continue = True  # set to False by /clear; restored after first fresh session
_current_info: dict = {}  # {cmd_type, task_id, started_at} while busy


# ── Claude config helpers ─────────────────────────────────────────────────────

def _resolve_config_path() -> str:
    """Return the real file path (resolves symlink if needed)."""
    if os.path.islink(_CLAUDE_CONFIG):
        return os.path.realpath(_CLAUDE_CONFIG)
    return _CLAUDE_CONFIG


def _validate_claude_config() -> bool:
    """Return True if ~/.claude.json exists and is valid JSON."""
    try:
        path = _resolve_config_path()
        if not os.path.exists(path):
            return False
        with open(path, "r") as f:
            json.load(f)
        return True
    except Exception:
        return False


def _backup_claude_config() -> None:
    """Save a timestamped backup of ~/.claude.json if it is valid. Keep last 5."""
    if not _validate_claude_config():
        return
    try:
        os.makedirs(_CLAUDE_BACKUPS_DIR, exist_ok=True)
        ts = int(time.time() * 1000)
        backup_path = os.path.join(_CLAUDE_BACKUPS_DIR, f".claude.json.backup.{ts}")
        shutil.copy2(_resolve_config_path(), backup_path)
        # Prune: keep only the 5 most recent backups
        all_backups = sorted(glob.glob(os.path.join(_CLAUDE_BACKUPS_DIR, ".claude.json.backup.*")))
        for old in all_backups[:-5]:
            try:
                os.remove(old)
            except Exception:
                pass
        print(f"[runner] config backed up → {os.path.basename(backup_path)}", flush=True)
    except Exception as e:
        print(f"[runner] backup failed: {e}", flush=True)


def _restore_claude_config() -> bool:
    """Restore ~/.claude.json from the most recent valid backup. Returns True on success."""
    backups = sorted(
        glob.glob(os.path.join(_CLAUDE_BACKUPS_DIR, ".claude.json.backup.*")),
        reverse=True,
    )
    for backup in backups:
        try:
            with open(backup, "r") as f:
                json.load(f)  # validate
            shutil.copy2(backup, _resolve_config_path())
            print(f"[runner] restored config from {os.path.basename(backup)}", flush=True)
            return True
        except Exception:
            continue
    print("[runner] no valid backup found for ~/.claude.json", flush=True)
    return False


# ── Claude runner ─────────────────────────────────────────────────────────────

def _run_claude(prompt: str, cmd_info: dict | None = None) -> None:
    global _process, _use_continue

    with _lock:
        use_cont = _use_continue
        _current_info.update(cmd_info or {})

    _backup_claude_config()

    args = ["claude", "--dangerously-skip-permissions"]
    if use_cont:
        args.append("--continue")
    args += ["-p", prompt]

    print(f"[runner] spawn: claude {'--continue ' if use_cont else ''}(prompt len={len(prompt)})", flush=True)
    proc = subprocess.Popen(args, cwd=WORKSPACE)

    with _lock:
        _process = proc

    proc.wait()
    print(f"[runner] claude exited (code={proc.returncode})", flush=True)

    if proc.returncode != 0 and not _validate_claude_config():
        print("[runner] ~/.claude.json corrupted — attempting auto-restore", flush=True)
        _restore_claude_config()

    with _lock:
        _process = None
        _current_info.clear()
        # After a fresh session completes, re-enable --continue so subsequent
        # messages continue that new session. After a normal session, state is
        # already True so this is a no-op.
        if not use_cont:
            _use_continue = True


def _spawn(prompt: str, cmd_info: dict | None = None) -> None:
    threading.Thread(target=_run_claude, args=(prompt, cmd_info), daemon=True).start()


def _post_btw(cmd: dict) -> None:
    """Re-post a free_text command as btw type and ack the original."""
    import urllib.request
    tracker = os.environ.get("TRACKER_URL", "http://tracker:3333")
    payload = cmd.get("payload", {})
    # Post new btw command
    body = json.dumps({"type": "btw", "payload": payload, "source": "telegram"}).encode()
    try:
        req = urllib.request.Request(
            f"{tracker}/api/commands",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[runner] btw post failed: {e}", flush=True)
    # Ack the original so it doesn't get processed again
    try:
        ack = urllib.request.Request(
            f"{tracker}/api/commands/{cmd['id']}/ack",
            data=json.dumps({"status": "done"}).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        urllib.request.urlopen(ack, timeout=5)
    except Exception as e:
        print(f"[runner] btw ack failed: {e}", flush=True)


def _load_git_workflow() -> str:
    """Load git workflow rules to attach to remote agent prompts."""
    try:
        with open(GIT_WORKFLOW_PATH, "r") as f:
            return f.read()
    except Exception as e:
        print(f"[runner] failed to load git-workflow.md: {e}", flush=True)
        return ""


def _attach_git_workflow(prompt: str) -> str:
    """Append git workflow instructions to the prompt for remote mode."""
    rules = _load_git_workflow()
    if not rules:
        return prompt
    return (
        f"{prompt}\n\n"
        f"---\n"
        f"## GIT WORKFLOW INSTRUCTIONS (remote mode)\n\n"
        f"You are running in REMOTE mode via the Telegram pipeline.\n"
        f"Follow these git workflow rules for branch management, PRs, and merging.\n"
        f"Use `tools/runtime/scripts/notify.sh` to communicate with the project owner.\n\n"
        f"{rules}"
    )


# Commands that trigger pipelines requiring git workflow
_GIT_WORKFLOW_COMMANDS = {"/develop", "/fix", "/refactor", "/next", "/review", "/research", "/analyze", "/epic", "/figma", "/design"}


def _needs_git_workflow(cmd: dict) -> bool:
    """Check if this command type needs git workflow instructions attached."""
    cmd_type = cmd.get("type", "")
    # Pipeline commands and task execution always need git workflow
    if cmd_type in ("next_task", "review"):
        return True
    if cmd_type == "claude_command":
        command = cmd.get("payload", {})
        if isinstance(command, str):
            try:
                command = json.loads(command)
            except Exception:
                command = {}
        if isinstance(command, dict):
            command = command.get("command", "")
        # Check if the command starts with a pipeline slash command
        for prefix in _GIT_WORKFLOW_COMMANDS:
            if command.startswith(prefix):
                return True
    return False


def _expand_slash_command(prompt: str) -> str:
    """Expand /command args into the full command-file content + args.

    In non-interactive (-p) mode Claude doesn't resolve slash commands
    from .claude/commands/, so we read the .md file and inline it.
    """
    stripped = prompt.strip()
    if not stripped.startswith("/"):
        return prompt

    parts = stripped.split(None, 1)
    cmd_name = parts[0][1:]  # drop leading '/'
    args = parts[1] if len(parts) > 1 else ""

    cmd_file = os.path.join(COMMANDS_DIR, f"{cmd_name}.md")
    if not os.path.isfile(cmd_file):
        return prompt  # no matching file — leave as-is

    with open(cmd_file, "r") as f:
        body = f.read()

    print(f"[runner] expanded /{cmd_name} from {cmd_file}", flush=True)

    expanded = body
    if args:
        expanded += f"\n\n---\n\n**User input:** {args}"
    return expanded


def _build_prompt(cmd: dict) -> str | None:
    cmd_type = cmd.get("type", "")
    payload = cmd.get("payload", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    prompt = None

    if cmd_type == "free_text":
        text = payload.get("text", "")
        prompt = (
            f"Message from the project owner via Telegram:\n\n"
            f"{text}\n\n"
            f"Process the request and send the response back via "
            f"tools/runtime/scripts/notify.sh \"your response\""
        )
    elif cmd_type == "claude_command":
        raw = payload.get("command", "")
        prompt = _expand_slash_command(raw)
    elif cmd_type == "next_task":
        task_id = payload.get("task_id", "")
        if task_id:
            prompt = _expand_slash_command(f"/next {task_id}")
        else:
            prompt = _expand_slash_command("/next")
    elif cmd_type == "review":
        feedback = payload.get("feedback", payload.get("text", ""))
        prompt = _expand_slash_command(f"/review {feedback}")
    elif cmd_type == "clear_session":
        return None  # handled separately
    else:
        return None

    # Attach git workflow rules for pipeline commands in remote mode
    if prompt and _needs_git_workflow(cmd):
        prompt = _attach_git_workflow(prompt)

    return prompt


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

    def do_GET(self) -> None:
        if self.path == "/status":
            with _lock:
                busy = _process is not None and _process.poll() is None
                info = dict(_current_info) if busy else {}
            self._send(200, {"busy": busy, **info})
        elif self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        global _use_continue

        if self.path == "/trigger":
            cmd = self._body()
            cmd_type = cmd.get("type", "")

            if cmd_type == "clear_session":
                with _lock:
                    if _process and _process.poll() is None:
                        _process.terminate()
                    _use_continue = False
                self._send(200, {"status": "cleared"})
                return

            prompt = _build_prompt(cmd)
            if not prompt:
                self._send(400, {"error": "unhandled command type"})
                return

            with _lock:
                busy = _process is not None and _process.poll() is None

            if busy:
                if cmd_type == "free_text" and cmd.get("id"):
                    # Re-post as btw so Claude picks it up between steps
                    _post_btw(cmd)
                    print(f"[runner] busy — reposted as btw", flush=True)
                else:
                    print(f"[runner] busy — command queued in tracker", flush=True)
                self._send(200, {"status": "queued"})
                return

            # Extract task_id from payload for status tracking
            payload = cmd.get("payload", {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            cmd_info = {
                "cmdType": cmd_type,
                "taskId": payload.get("task_id") or None,
                "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _spawn(prompt, cmd_info)
            self._send(200, {"status": "started"})

        elif self.path == "/clear":
            with _lock:
                if _process and _process.poll() is None:
                    _process.terminate()
                _use_continue = False
            self._send(200, {"status": "cleared"})

        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args) -> None:
        print(f"[runner] {fmt % args}", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[runner] listening on :{PORT}", flush=True)
    server.serve_forever()
