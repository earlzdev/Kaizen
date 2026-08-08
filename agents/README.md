# agents/

The agents that sit on top of Kaizen. `agents/core` is a reusable library;
every concrete agent (a persona + a connector to some chat surface) is built
from it.

```
agents/
├── core/     the shared agent library — LLM backends, the tool-use loop,
│             history, enrollment, prompt templates, multi-language support
└── kaya/     Кая — the Telegram companion built on agents/core
```

## agents/core

What every agent reuses, so a new one is "a soul + a connector", not a
rewrite of the harness:

- `llm.py` / `cli.py` — two interchangeable LLM backends: the Anthropic API
  (per-token billing) or the `claude` CLI logged into a Max subscription.
- `agent.py` / `loop.py` / `runner.py` — the tool-use loop: build the system
  prompt, call the model, run tools against Brain, self-check long or
  researched answers before they go out.
- `soul.py` — loads an agent's persona from a `soul.md` file. The persona is
  data, not code: two agents built from the same library feel completely
  different because their souls differ, not their Python.
- `prompts.py` — the harness-level prompt text every agent shares (the
  self-wake framing, the tracker-event framing, the research protocol, the
  self-check instructions). This is model-facing instruction text, not
  something a human ever reads directly — Claude follows it fine in English
  regardless of what language the surrounding conversation is in, so it's
  written in English outright rather than translated per language.
- `cliches.py` / `locale.py` / `strings.py` — see **Multi-language support**
  below.
- `enroll.py` / `mcp_client.py` — pairing with Brain and calling its tools
  over MCP.

## Multi-language support

An agent built on Agent Core can run its **model-facing** text (prompts.py)
in English always — Claude doesn't need translated instructions. What it
can't do that with is the text a connector sends **straight to a human**:
error messages, status lines, the persona itself. That text lives in
per-language files under a `locales/<lang>/` directory, one at the agent's
own package root and one shared at `agents/core/locales/` for anything
every agent uses (currently the cliché map).

```
agents/core/locales/<lang>/
    cliches.json     shared AI-cliché map (self-check style rules)
    strings.json      shared user-facing strings (e.g. the CLI quota message)

agents/<agent>/locales/<lang>/
    soul.md            the persona, in this language
    strings.json        this agent's own user-facing strings
```

An agent selects its language with its own config setting (Кая's is
`KAYA_LANGUAGE`, default `"en"`). At boot, before building anything, it calls
`agents.core.locale.require_language(language, roots)` with every locale
root it depends on. **If any required file is missing for that language, the
agent refuses to start** and logs the exact missing paths — a
half-translated language fails loudly at boot, not silently mid-conversation
with a persona in French quoting Russian error messages.

A **present-but-corrupt** file (bad JSON) is a different, smaller problem:
`cliches.py` still logs and continues with an empty cliché map rather than
crashing, since losing the self-check's style rules is not worth taking the
agent down for.

This covers the agent's own text; tools are separate (they have no Brain
access and don't know which agent is calling), so each tool that produces
language-sensitive output takes its own optional `language` argument
(`en`/`ru`, default `en`) instead: `weather`, `traffic_score`, `route_time`,
`cheapest_flights`, and `youtube_transcript` all support it — pass a
`language` matching whatever you're currently replying in and the tool
picks the matching output (`cheapest_flights` also switches currency,
USD/RUB). The underlying data source stays Russian where it has to
(Yandex Maps' city lookups and scraped page format, for instance) — only
each tool's own rendered output is bilingual.

### Adding a language to an agent

1. Create `<agent>/locales/<lang>/` and `agents/core/locales/<lang>/` (if it
   doesn't exist yet) and add every required file — copy an existing
   language's files as the template and translate their *content*, not just
   the strings: `cliches.json` needs cliché patterns that are actually
   idiomatic AI-slop in the target language, not literal translations of the
   Russian or English ones.
2. Set the agent's language setting to the new code and boot it. A missing
   file shows up immediately as a fatal startup error naming the exact path.

See `agents/kaya/README.md` for Кая's specific file list and an example.
