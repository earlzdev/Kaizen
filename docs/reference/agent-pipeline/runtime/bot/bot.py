import os
import logging
import httpx
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TRACKER_URL = os.environ.get("TRACKER_URL", "http://tracker:3333")
ALLOWED_CHAT_ID = int(os.environ["ALLOWED_CHAT_ID"])

TYPE_EMOJI = {"feature": "🔵", "bugfix": "🔴", "refactor": "♻️", "brainstorm": "🧠"}
STATUS_EMOJI = {"pending": "⏳", "in_progress": "⚙️", "review": "👀", "done": "✅", "blocked": "🚫"}

# Per-chat pending command state: chat_id -> command type waiting for text
_pending: dict[int, str] = {}


def allowed(update: Update) -> bool:
    return update.effective_chat.id == ALLOWED_CHAT_ID


# ── Menus ─────────────────────────────────────────────────────────────────────

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("— MONITORING —", callback_data="noop")],
        [
            InlineKeyboardButton("Tasks", callback_data="m:status"),
            InlineKeyboardButton("Queue", callback_data="m:queue"),
        ],
        [
            InlineKeyboardButton("Agents", callback_data="m:agents"),
            InlineKeyboardButton("Busy?", callback_data="m:busy"),
        ],
        [InlineKeyboardButton("— CONTROL —", callback_data="noop")],
        [
            InlineKeyboardButton("Next", callback_data="c:next"),
            InlineKeyboardButton("New task →", callback_data="m:new-task"),
        ],
        [InlineKeyboardButton("PR feedback", callback_data="c:review")],
        [InlineKeyboardButton("— — —", callback_data="noop")],
        [
            InlineKeyboardButton("Reset context", callback_data="c:clear"),
            InlineKeyboardButton("🛑 Abort", callback_data="c:abort"),
        ],
    ])


def new_task_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Develop", callback_data="c:develop"),
            InlineKeyboardButton("Fix", callback_data="c:fix"),
        ],
        [
            InlineKeyboardButton("Refactor", callback_data="c:refactor"),
            InlineKeyboardButton("Epic", callback_data="c:epic"),
        ],
        [InlineKeyboardButton("Brainstorm", callback_data="c:brainstorm")],
        [InlineKeyboardButton("🎨 Figma to Code", callback_data="c:figma")],
        [InlineKeyboardButton("🖌 Design mockups", callback_data="c:design")],
        [InlineKeyboardButton("🔬 Research", callback_data="c:research")],
        [InlineKeyboardButton("📋 Analyze", callback_data="c:analyze")],
        [InlineKeyboardButton("Cancel", callback_data="close")],
    ])


# ── Tracker helpers ────────────────────────────────────────────────────────────

async def fetch_tasks(status: str | None = None) -> list:
    params = {"status": status} if status else {}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{TRACKER_URL}/api/tasks", params=params)
        r.raise_for_status()
    return r.json()


async def post_command(cmd: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{TRACKER_URL}/api/commands", json=cmd)
        r.raise_for_status()
    return r.json()


async def get_runner_status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{TRACKER_URL}/api/runner/status")
            r.raise_for_status()
        return r.json()
    except Exception:
        return {}


async def send_claude(command: str) -> None:
    await post_command({"type": "claude_command", "payload": {"command": command}, "source": "telegram"})


def fmt_task(task: dict) -> str:
    icon = TYPE_EMOJI.get(task.get("type", ""), "▪️")
    s_icon = STATUS_EMOJI.get(task.get("status", ""), "❓")
    title = (task.get("title") or task.get("id", "—"))
    return f"{icon} {s_icon} {title}"


# ── /start and /menu ───────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    await update.message.reply_text("👋 YourProject Agent — ready to work.", reply_markup=main_menu())


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    await update.message.reply_text("Menu:", reply_markup=main_menu())


# ── Callback query handler ─────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.message.chat_id != ALLOWED_CHAT_ID:
        await q.answer()
        return

    await q.answer()
    data = q.data
    chat_id = q.message.chat_id

    if data == "noop":
        return

    if data == "close":
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    # ── Monitoring ──────────────────────────────────────────────────────────────

    if data == "m:status":
        try:
            tasks = await fetch_tasks()
            active = [t for t in tasks if t.get("status") in ("in_progress", "review", "blocked")]
            if not active:
                await q.message.reply_text("No active tasks.")
                return
            lines = ["<b>Active tasks:</b>"]
            buttons = []
            for t in active:
                lines.append(fmt_task(t))
                tid = t["id"]
                buttons.append([InlineKeyboardButton("✅ Done", callback_data=f"done:{tid}")])
            buttons.append([InlineKeyboardButton("❌ Close", callback_data="close")])
            await q.message.reply_text(
                "\n".join(lines),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            )
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {e}")

    elif data == "m:queue":
        try:
            tasks = await fetch_tasks("pending")
            if not tasks:
                await q.message.reply_text("Queue is empty.")
                return
            lines = ["<b>Queue (pending):</b>"]
            buttons = []
            for i, t in enumerate(tasks, 1):
                icon = TYPE_EMOJI.get(t.get("type", ""), "▪️")
                title = (t.get("title") or t.get("id", "—"))
                lines.append(f"{i}. {icon} {title}")
                label = title[:30]
                buttons.append([InlineKeyboardButton(f"▶️ {label}", callback_data=f"sel:{t['id']}")])
            buttons.append([InlineKeyboardButton("❌ Close", callback_data="close")])
            await q.message.reply_text(
                "\n".join(lines),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {e}")

    elif data == "m:agents":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                runner_resp = await client.get(f"{TRACKER_URL}/api/runner/status")
                runner_resp.raise_for_status()
                runner = runner_resp.json()

            lines = ["<b>🏗 Agent status</b>", ""]

            # Runner status (primary info)
            if runner.get("busy"):
                lines.append("⚙️ <b>Agent is working</b>")
                task_id = runner.get("taskId")
                cmd_type = runner.get("cmdType")
                started = runner.get("startedAt")
                if cmd_type:
                    lines.append(f"  Command: <code>{cmd_type}</code>")
                if task_id:
                    lines.append(f"  Task: <code>{task_id}</code>")
                if started:
                    lines.append(f"  Started: {started}")
            else:
                lines.append("💤 <b>Agent is idle</b>")

            if runner.get("error"):
                lines.append(f"\n⚠️ Runner: {runner['error']}")
            if not runner.get("configured", True):
                lines.append("\n⚠️ Agent-runner not configured")

            # Show active agent statuses from YAML (multi-agent tasks)
            agents_resp = None
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    agents_resp = await client.get(f"{TRACKER_URL}/api/agents")
                    agents_resp.raise_for_status()
                agents = agents_resp.json()
                active_agents = [a for a in agents if a.get("status") not in ("idle", None)]
            except Exception:
                active_agents = []

            if active_agents:
                STATUS_ICON = {
                    "in_progress": "⚙️", "review": "👀", "blocked": "🚫",
                    "done": "✅", "completed": "✅", "decomposing": "🔍",
                    "implementing": "🔨", "waiting": "⏳",
                }
                lines.append("")
                lines.append("<b>Active roles:</b>")
                for a in active_agents:
                    name = a.get("name", a.get("slug", "—"))
                    status = a.get("status", "")
                    s_icon = STATUS_ICON.get(status, "❓")
                    task_id = a.get("taskId")
                    progress = a.get("progress")
                    line = f"  {s_icon} <b>{name}</b> — {status}"
                    if task_id:
                        line += f" [<code>{task_id}</code>]"
                    lines.append(line)
                    if progress:
                        lines.append(f"       <i>{progress}</i>")

            await q.message.reply_text("\n".join(lines).strip(), parse_mode="HTML")
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {e}")

    elif data == "m:busy":
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{TRACKER_URL}/api/runner/status")
                r.raise_for_status()
            d = r.json()
            await q.message.reply_text("⚙️ Agent is currently working." if d.get("busy") else "💤 Agent is idle.")
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {e}")

    # ── Task selection from list ────────────────────────────────────────────────

    elif data.startswith("sel:"):
        task_id = data[4:]
        try:
            await post_command({"type": "next_task", "payload": {"task_id": task_id}, "source": "telegram"})
            await q.message.reply_text(f"⚙️ Agent is taking task <code>{task_id}</code>.", parse_mode="HTML")
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {e}")

    elif data.startswith("done:"):
        task_id = data[5:]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.put(
                    f"{TRACKER_URL}/api/tasks/{task_id}",
                    json={"status": "done"},
                )
                r.raise_for_status()
            await q.message.reply_text(f"✅ Task <code>{task_id}</code> closed.", parse_mode="HTML")
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {e}")

    # ── Control: immediate actions ──────────────────────────────────────────────

    elif data == "c:next":
        try:
            await post_command({"type": "next_task", "source": "telegram"})
            await q.message.reply_text("⚙️ Agent is taking the next task from the queue.")
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {e}")

    elif data == "c:clear":
        try:
            await post_command({"type": "clear_session", "source": "telegram"})
            await q.message.reply_text("🗑 Context reset. The next session will start fresh.")
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {e}")

    elif data == "c:abort":
        try:
            await send_claude("/abort")
            await q.message.reply_text("🛑 /abort command sent — the agent will drop its current work and return to main.")
        except Exception as e:
            await q.message.reply_text(f"❌ Error: {e}")

    # ── Control: commands requiring text input ──────────────────────────────────

    elif data == "m:new-task":
        await q.message.reply_text("Choose a task type:", reply_markup=new_task_menu())

    elif data in ("c:develop", "c:fix", "c:refactor", "c:epic", "c:brainstorm", "c:review", "c:research", "c:analyze", "c:figma", "c:design"):
        prompts = {
            "c:develop":    ("develop",    "<b>Develop</b>\n\nDescribe the feature:"),
            "c:fix":        ("fix",        "<b>Fix</b>\n\nDescribe the bug and where it occurs:"),
            "c:refactor":   ("refactor",   "<b>Refactor</b>\n\nDescribe what to refactor and why:"),
            "c:epic":       ("epic",       "<b>Epic</b>\n\nDescribe the large feature. Xavier will decompose it into tasks and clarify questions:"),
            "c:brainstorm": ("brainstorm", "<b>Brainstorm</b>\n\nDescribe the topic to analyze:"),
            "c:review":     ("review",     "<b>Review</b>\n\nWrite the PR feedback.\nThe agent will fix it, amend, and force-push:"),
            "c:research":   ("research",   "<b>🔬 Research</b>\n\nDescribe the topic to research.\nThe agent will do a deep analysis with web search and open a PR with the report:"),
            "c:analyze":    ("analyze",    "<b>📋 Analyze</b>\n\nDescribe the feature/flow to document.\nAda Lovelace will produce a living spec (for mobile — with screens and transitions):"),
            "c:figma":      ("figma",      "<b>🎨 Figma to Code</b>\n\nSend a link to the Figma mockup and describe what to build:"),
            "c:design": ("design", "<b>🖌 Design mockups</b>\n\nDescribe which mockups to create, fix, or refine:"),
        }
        cmd_type, prompt_text = prompts[data]
        _pending[chat_id] = cmd_type
        await q.message.reply_text(
            f"{prompt_text}\n\n<i>Send /cancel to cancel.</i>",
            parse_mode="HTML",
        )


# ── Text message handler ───────────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Cancel
    if text.lower() in ("/cancel", "cancel"):
        if chat_id in _pending:
            _pending.pop(chat_id)
            await update.message.reply_text("❌ Cancelled.")
        else:
            await update.message.reply_text("Nothing to cancel.")
        return

    # Pending command waiting for text
    if chat_id in _pending:
        cmd_type = _pending.pop(chat_id)
        try:
            runner = await get_runner_status()
            was_busy = runner.get("busy", False)
            if cmd_type == "review":
                await post_command({
                    "type": "review",
                    "payload": {"feedback": text},
                    "source": "telegram",
                })
            else:
                await send_claude(f"/{cmd_type} {text}")
            short = text[:50] + ("..." if len(text) > 50 else "")
            if was_busy:
                await update.message.reply_text(
                    f"✉️ <code>/{cmd_type}</code> delivered. Agent is busy — will run after the current task.\n{short}",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    f"✉️ <code>/{cmd_type}</code> delivered, agent is starting.\n{short}",
                    parse_mode="HTML",
                )
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return

    # Check if there's a pending research question — route answer to researcher
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{TRACKER_URL}/api/research/pending")
            if r.status_code == 200:
                qa = r.json()
                await client.post(
                    f"{TRACKER_URL}/api/research/answer/{qa['id']}",
                    json={"answer": text},
                )
                await update.message.reply_text("✉️ Answer forwarded to the researcher.")
                return
    except Exception:
        pass  # No pending question or tracker unavailable — continue normally

    # Free text → relay to agent
    try:
        runner = await get_runner_status()
        was_busy = runner.get("busy", False)
        await post_command({
            "type": "free_text",
            "payload": {"text": text},
            "source": "telegram",
        })
        if was_busy:
            await update.message.reply_text(
                "✉️ Delivered. The agent is busy right now — will process it between steps."
            )
        else:
            await update.message.reply_text("✉️ Delivered, agent is processing.")
    except Exception as e:
        await update.message.reply_text(f"❌ Tracker unavailable: {e}")


# ── Legacy slash commands ──────────────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in _pending:
        _pending.pop(chat_id)
        await update.message.reply_text("❌ Cancelled.")
    else:
        await update.message.reply_text("Nothing to cancel.")


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    try:
        await post_command({"type": "next_task", "source": "telegram"})
        await update.message.reply_text("⚙️ Agent is taking the next task.")
    except Exception:
        await update.message.reply_text("❌ Tracker unavailable.")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    try:
        await post_command({"type": "clear_session", "source": "telegram"})
        await update.message.reply_text("🗑 Context reset.")
    except Exception:
        await update.message.reply_text("❌ Tracker unavailable.")


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    topic = " ".join(context.args).strip()
    if not topic:
        _pending[update.effective_chat.id] = "research"
        await update.message.reply_text(
            "<b>🔬 Research</b>\n\nDescribe the topic to research:\n\n"
            "<i>Send /cancel to cancel.</i>",
            parse_mode="HTML",
        )
        return
    try:
        runner = await get_runner_status()
        was_busy = runner.get("busy", False)
        await send_claude(f"/research {topic}")
        short = topic[:60] + ("..." if len(topic) > 60 else "")
        if was_busy:
            await update.message.reply_text(
                f"🔬 Research queued (agent is busy):\n{short}",
            )
        else:
            await update.message.reply_text(f"🔬 Starting research:\n{short}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    topic = " ".join(context.args).strip()
    if not topic:
        _pending[update.effective_chat.id] = "analyze"
        await update.message.reply_text(
            "<b>📋 Analyze</b>\n\nDescribe the feature/flow to document.\n"
            "For mobile — the spec will include screens and transitions.\n\n"
            "<i>Send /cancel to cancel.</i>",
            parse_mode="HTML",
        )
        return
    try:
        runner = await get_runner_status()
        was_busy = runner.get("busy", False)
        await send_claude(f"/analyze {topic}")
        short = topic[:60] + ("..." if len(topic) > 60 else "")
        if was_busy:
            await update.message.reply_text(
                f"📋 Analysis queued (agent is busy):\n{short}",
            )
        else:
            await update.message.reply_text(f"📋 Starting analysis:\n{short}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not allowed(update):
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await update.message.reply_text(
            "Usage: /task [type:] description\n"
            "Example: /task bugfix: login is crashing"
        )
        return
    TASK_TYPES = ("feature", "bugfix", "refactor", "brainstorm")
    task_type = "feature"
    title = raw
    for prefix in TASK_TYPES:
        if raw.lower().startswith(f"{prefix}:"):
            task_type = prefix
            title = raw[len(prefix) + 1:].strip()
            break
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{TRACKER_URL}/api/tasks",
                json={"title": title, "type": task_type, "description": ""},
            )
            r.raise_for_status()
        data = r.json()
        task_id = data.get("id", "—")
        position = data.get("position") or data.get("queue_position", "?")
        icon = TYPE_EMOJI.get(task_type, "▪️")
        await update.message.reply_text(
            f"✅ Task created\n\n{icon} [{task_type}] {title}\n"
            f"ID: <code>{task_id}</code>\nPosition: #{position}",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ── Bot setup ──────────────────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("menu", "Open the control menu"),
        BotCommand("next", "Take the next task from the queue"),
        BotCommand("task", "Add a task: /task [type:] description"),
        BotCommand("research", "Deep research: /research topic"),
        BotCommand("analyze", "Document a feature: /analyze topic"),
        BotCommand("cancel", "Cancel the current command"),
        BotCommand("clear", "Reset the agent's session context"),
    ])


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("task", cmd_task))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started. Allowed chat: %s", ALLOWED_CHAT_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
