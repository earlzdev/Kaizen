# Kaizen

<img src="kaizen.jpeg" alt="Kaizen" width="220">

*Kaizen* (改善) is Japanese for **continuous improvement** — the idea that
lasting progress comes from small improvements accumulated over time.

Kaizen applies that idea to AI assistants.

Instead of treating every conversation as a fresh start, Kaizen builds a
continuously evolving understanding of you: what matters to you, what
you're working on, the decisions you've made, the habits you've built, and
the things you don't want to explain twice.

That understanding belongs to **the system**, not to a single chat or a
single agent.

Agents come and go. Tools change. Models improve.

Your context stays.

At the center of Kaizen is **Brain** — a self-hosted service that owns
this long-term context and gives agents access to memory, reminders, web
search, research, and other tools through a common interface.

**Kaya**, the included Telegram assistant, is the first agent built on top
of Brain. She can remember, plan, remind, research, and help with ongoing
work because she always starts from what the system already knows about
you.

The result isn't simply an assistant with memory.

It's a system that becomes more useful the longer you use it.

Runs on Claude, on your own Docker Compose stack, in your own Postgres. No
SaaS, no accounts, no one else's servers.

Stack: aiogram 3 · Anthropic Claude (API *or* Max subscription via the
`claude` CLI) · PostgreSQL + pgvector · SQLAlchemy async · gRPC · no
LangChain.

---

## Quickstart

Requires Docker and Docker Compose.

```bash
cp .env.example .env      # fill in tokens, ALLOWED_USER_IDS, TIMEZONE
make up                   # or: make up dev / make up prod
```

`make up` builds, starts the stack, and walks you through approving any
agent enrollment requests right there in the terminal. Also: `make down`,
`make logs`, `make ps`, `make psql`, `make approve` (re-run the enrollment
approval on its own). Reaching for `docker compose` directly instead? The
compose file lives at `deploy/docker-compose.yml`, not the repo root, so
every call needs `--env-file .env -f deploy/docker-compose.yml`.

For the Max-subscription backend (no per-token billing), log in once:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml exec kaya claude      # follow the login prompts
docker compose --env-file .env -f deploy/docker-compose.yml restart kaya          # picks up the login immediately
```

Kaya runs in English by default; set `KAYA_LANGUAGE=ru` in `.env` for
Russian. See `agents/kaya/README.md` for what's language-configurable and
how to add another language.

**Give her a head start.** Kaya learns about you as you talk, but you don't
have to build that up one message at a time — bulk-load a paragraph of
context in one shot:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml cp profile.txt brain:/tmp/profile.txt
docker compose --env-file .env -f deploy/docker-compose.yml exec brain \
  python -m brain.seed_profile /tmp/profile.txt
```

where `profile.txt` looks like:

```
# Profile
timezone: Europe/Lisbon
home_location: Lisbon, Portugal

# Facts
Works as a backend engineer, focused on distributed systems.
Prefers dark roast coffee, no sugar.
- Learning Japanese, practices 20 minutes daily.
```

This writes straight into Brain's shared memory — no model in the loop, so
write facts the way `brain/seed_profile.py`'s header describes (one per
line, third person, self-contained) if you want them indistinguishable from
what Kaya saves herself.

---

## Tools

Every agent built on `agents/core` — Kaya today, others later — reaches the
same set of tools through Brain: web search and page reading, weather,
travel time and live traffic, cheapest flights, and YouTube transcripts.
Ask Kaya to look something up and she searches properly, not once — several
differently-phrased queries, real page reads rather than trusting snippets —
and runs a self-check pass afterward that verifies her draft's claims
against sources she actually opened. New tools just get dropped into
`tools/`, one directory each, and every agent gets them automatically.

---

## Your data

Everything Kaya knows lives in Postgres: Brain's `facts`, `profile`,
`reminders`, `episodes`, and Kaya's own `messages` — one logical DB per
service (`brain`, `kaya`, ...), one Postgres instance.

### Backups

A `backup` service in the compose stack runs `pg_dumpall` on a schedule,
gzips it, encrypts it with [age](https://age-encryption.org/) (public-key
only — the private key never touches this machine), and uploads it to a
Yandex Object Storage bucket you configure in `.env`. Trigger one manually:

```bash
./infra/scripts/backup.sh
```

It prints the S3 key. List existing backups from the admin panel's Backups
card, or the backup service's own internal API.

### Restore — destructive

```bash
./infra/scripts/restore.sh latest ~/keys/kaizen-age.txt   # or a specific S3 key
```

Needs the age **identity** (private key) you generated when you set up
`BACKUP_AGE_RECIPIENT` — keep it somewhere that isn't this machine. This
stops the app services, overwrites every database from the decrypted dump,
and restarts everything. Confirms before touching
anything.

---

## Moving to another machine

The state that matters is: **the database**, **`.env`**, the **age
identity** (private key, kept off both machines' backup path on purpose),
and **the Claude login**.

1. **On the old machine** — take a backup and note the S3 key it prints:
   ```bash
   ./infra/scripts/backup.sh
   ```

2. **On the new machine** — `git clone` the repo, copy across `.env` (it
   holds your tokens, `ALLOWED_USER_IDS`, and the S3/age config) and your
   age identity file, then bring the stack up:
   ```bash
   make up
   ```

3. **Restore** from the backup you just took:
   ```bash
   ./infra/scripts/restore.sh latest ~/keys/kaizen-age.txt
   ```
   This stops the app services, overwrites every database from the backup,
   and restarts them.

4. **Log in to Claude again** (the OAuth token is machine-bound —
   re-authenticating is cleaner than copying it):
   ```bash
   docker compose --env-file .env -f deploy/docker-compose.yml exec kaya claude
   docker compose --env-file .env -f deploy/docker-compose.yml restart kaya
   ```

5. **Verify** before trusting it:
   ```bash
   docker compose --env-file .env -f deploy/docker-compose.yml exec -T postgres \
     psql -U learnbot -d brain -c \
     "SELECT 'facts='||count(*) FROM facts UNION ALL SELECT 'reminders='||count(*) FROM reminders;"
   docker compose --env-file .env -f deploy/docker-compose.yml logs kaya | grep -E "LLM backend|MCP server|Run polling"
   ```
   (`make psql` drops you into an interactive `brain` session if you'd
   rather poke around by hand.) Then send Kaya a message —
   ask her what she
   remembers about you.

**Stop the old instance before the new one goes live.** Two bots polling
the same Telegram token will fight over updates and drop messages.

---

## Architecture

```
Telegram ──► Кая (agents/kaya) ──MCP/HTTP──► Brain ──gRPC──► modules
                    ▲                          │            tools/
                    └──── delivery push ───────┘
                            (reminders)
                                                       PostgreSQL + pgvector
                                                       (one DB per service)
```

- **`brain/`** — the gateway: authenticates agents, enforces the per-agent
  access-list, holds shared memory, routes tool calls to modules, fires
  reminders, and serves the admin panel.
- **`agents/`** — the agents built on top of Brain. `agents/core` is the
  reusable library (LLM backends, the tool-use loop, multi-language
  support); `agents/kaya` is Кая herself. See `agents/README.md`.
- **`tools/`** — stateless utility tools (web search, weather, travel time,
  ...), one directory per tool, auto-discovered and served to Brain over
  gRPC.
- **`infra/proto/`** — the frozen gRPC contract between Brain and modules,
  and its generated code.

A couple of internal modules (project tracker, a learning module) run on
the same Brain; they're private and not published here.

---

## License

[AGPL-3.0](LICENSE).
