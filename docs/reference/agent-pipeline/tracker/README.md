# YourProject Dev Tracker

Local task tracker and notification service for autonomous agent development.

## Setup

```bash
# 1. Install dependencies
cd tracker
npm install

# 2. Configure environment
cp ../.env.tracker ../.env.tracker.local
# Edit .env.tracker and fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID

# 3. Start the service
npm start
# → http://localhost:3333
```

## Usage

### Web Dashboard
Open http://localhost:3333 in your browser.

- **Create tasks** — click "New Task", fill in title, type, description
- **Reorder queue** — drag and drop rows to reprioritize
- **Agent Activity** — left panel shows live agent statuses from `docs/tracker/*/status/*.yml`
- **Send Command** — queue a command for the agent via the "Send Command" button

### REST API

All endpoints are at `http://localhost:3333/api`.

**Tasks:**
```bash
GET  /api/tasks               # list all tasks
POST /api/tasks               # create task
GET  /api/tasks/next          # next pending task (for agent)
PUT  /api/tasks/:id           # update task
PUT  /api/tasks/reorder       # reorder queue { ids: [...] }
DELETE /api/tasks/:id         # delete task
```

**Commands (external → agent):**
```bash
POST /api/commands            # send command from Telegram bot / curl
GET  /api/commands/next       # agent polls for next command
PUT  /api/commands/:id/ack    # agent acknowledges command
```

**Notifications (agent → Telegram):**
```bash
POST /api/notify              # { message: "..." } → sends Telegram message
```

**Real-time:**
```bash
GET  /api/events              # SSE stream (tasks, agents, commands, notify)
```

### Scripts (for agents)

```bash
# Notify via Telegram
scripts/notify.sh "Task completed: ..."

# Task operations
scripts/tracker.sh task:next
scripts/tracker.sh task:status <id> in_progress
scripts/tracker.sh task:pr <id> https://github.com/...
scripts/tracker.sh task:branch <id> task/my-feature

# Command operations
scripts/tracker.sh cmd:next
scripts/tracker.sh cmd:ack <id>
```

### Build verification

```bash
scripts/verify-build.sh backend    # docker build all services
scripts/verify-build.sh mobile     # mobile debug build
scripts/verify-build.sh all        # both
scripts/verify-build.sh backend service-a   # single service
```

## Telegram bot integration

Your Telegram bot just needs to call `POST /api/commands`:

```bash
curl -X POST http://localhost:3333/api/commands \
  -H "Content-Type: application/json" \
  -d '{"type": "next_task", "source": "telegram"}'

# Or with a message:
curl -X POST http://localhost:3333/api/commands \
  -H "Content-Type: application/json" \
  -d '{"type": "free_text", "payload": {"text": "PR looks good, take next task"}, "source": "telegram"}'
```

## Agent workflow

1. You create tasks in the web UI
2. You tell Claude `/next` (or the agent polls `GET /api/commands/next`)
3. Agent takes next task → implements → verifies build → creates PR
4. Agent calls `POST /api/notify` → you get a Telegram message with PR link
5. You review and merge the PR
6. You send a command (via Telegram bot or web UI) → agent picks up next task
