# Kaizen — project conventions

Personal multi-agent system for order and continuous improvement. Telegram
agent (Кая) + central Brain gateway + gRPC modules behind it. This repo ships
Brain, Кая, and a shared tools service — a private deployment can add more
gRPC modules the same way `tools` is wired in.

## Architecture (who talks to whom)
```
agents (Кая) ──MCP/HTTP──► brain (auth + access-list + shared memory + tool
router + reminder sweeper + admin panel /admin/panel)
brain ──gRPC (infra/proto module.proto: RegisterTools/CallTool)──►
  tools/ (stateless utility tools, one dir per tool, auto-discovered by loader)
brain ──HTTP push──► agent delivery receivers (reminders)
a module ──HTTP POST /event──► brain ──► the owner's agent (the ONE inversion:
  a module originating news, e.g. "this background job finished")
infra/backup ──pg_dumpall→gzip→age→Yandex S3 (private key stays OFFLINE)
```

## Layout
- `brain/`, `agents/` (`agents/core` = reusable agent lib; see
  `agents/README.md` and `agents/kaya/README.md`), `tools/` — product code
- `infra/` — `proto/` (the Brain↔modules gRPC contract + generated code),
  `modkit` (module-side tool runtime), `backup` (encrypted S3 backups),
  `migrations` (shared Alembic runner), `scripts/`
- `deploy/` — docker-compose (base=prod closed ports, dev=open) + `docker/`

## Hard rules
- **NEVER read/print `.env`** — real secrets. Use `.env.example` for reference/edits.
- Tech stack is strict: aiogram 3, Anthropic SDK, PostgreSQL+pgvector,
  SQLAlchemy async, gRPC (grpcio) between brain and modules; **no LangChain**.
- **Anthropic SDK only inside `agents/core`** (llm.py — API backend; cli.py wraps
  the `claude` CLI for the Max backend). Modules/tools/brain never call an LLM.
- **Service isolation:** services import only their own package + shared `infra`
  libs (`infra.proto.gen` — the gRPC contract; `infra.modkit` — ToolDef/servicer
  runtime), and agents import `agents.core`. Never import across services.
- **DB-per-service** (one Postgres instance): `brain`, `kaya` logical DBs (add
  one per new module the same way). No cross-service FKs/JOINs.
- **Migrations are mandatory** (`infra/migrations/`): one shared Alembic runner,
  one version chain per service, applied at boot by that service's
  `create_tables()`. `metadata.create_all` is gone — it only ever created
  MISSING TABLES, so every column added after a database existed stayed missing
  until a query named it, which cost a production outage. Change a model →
  `make migration svc=<service> m="what changed"`, review the generated file,
  commit it with the model change. Never edit an applied revision; write a new
  one. Adding a new module needs a new entry in `infra/migrations/registry.py`
  and a `versions/<service>/` directory.
- Every new module gets a WHAT/WHY/HOW comment header.
- Every behavior-affecting change gets a one-line entry (what + why, newest
  first) in that component's own CHANGELOG.md — `agents/kaya/CHANGELOG.md`
  for Кая, `brain/CHANGELOG.md` for Brain, `tools/CHANGELOG.md` for the
  shared tools, one per module as they grow enough history to warrant it.
  Internal-only plumbing (caching, retries, query tuning — nothing an agent
  or owner would notice) belongs in the commit message, not the changelog.
- Claude prompts live in the owning service's own prompts module (e.g.
  `agents/core/prompts.py`); agent personas are `soul.md` files (data, not code).
- **Multi-language text lives under `locales/<lang>/`**, never inline: an
  agent's persona, its cliché map, and every string it sends an owner
  directly are per-language files (`agents/kaya/locales/`,
  `agents/core/locales/`), loaded by language code and checked for
  completeness at boot (`agents.core.locale.require_language` — a
  half-translated language must fail loudly at startup, not degrade
  file-by-file mid-conversation). Model-facing instruction text
  (`agents/core/prompts.py`) is the one exception — Claude reads English
  instructions fine regardless of conversation language, so it stays plain
  English with no locale variants.
- RAG is manual (embed → pgvector cosine → inject); embedding model is
  `paraphrase-multilingual-MiniLM-L12-v2`, 384 dims everywhere.

## Auth model
- Agents pair via **enrollment**: agent asks Brain to connect → owner approves in
  the terminal (`make approve`) → token issued once, stored by the agent
  (Кая: `kaya-state` volume). Self-healing: rejected/stale tokens auto re-enroll.
  No tokens in `.env` needed for agents.
- `BRAIN_ADMIN_TOKEN` = admin panel/API; `DELIVERY_TOKEN` = Brain→agent pushes;
  `MODULE_EVENT_TOKEN` = a module pushing news to Brain.
- Кая CLI backend: `DISABLE_AUTOUPDATER=1` is CRITICAL (the CLI otherwise
  "migrates" itself into the mounted volume and a volume wipe deletes it).

## Commands
- `make up` / `make up dev` / `make up prod` — one command: build+start, then
  approve pending agents in-terminal. `make approve`, `make down`, `make logs`,
  `make ps`, `make psql`.
- Compose must run with `--env-file .env` from repo root (Makefile/scripts do).
- One-time Max login: `docker compose --env-file .env -f deploy/docker-compose.yml exec kaya claude`
- Backups: `infra/scripts/backup.sh`; restore: `infra/scripts/restore.sh <s3-key> <age-identity>` (destructive).

## Verification
Runtime = docker (no local venv): `make up`, watch `make logs`. The user is
learning from this codebase — explain non-obvious decisions in comments.
