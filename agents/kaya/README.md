# Кая (Kaya)

The Telegram-facing agent: a persona (`soul.md`) plus a connector, built on
`agents/core`. See `agents/README.md` for the shared library her code reuses
and for how multi-language support works in general — this file covers
Кая's specific pieces.

## Running her

```bash
cp .env.example .env      # fill in tokens, ALLOWED_USER_IDS, TIMEZONE
make up
```

See the repo root `README.md` for the full setup (backends, Max-subscription
login, backups).

## Language

Кая runs in English by default. To run her in Russian instead, set:

```bash
KAYA_LANGUAGE=ru
```

in `.env`. This picks `agents/kaya/locales/ru/` and
`agents/core/locales/ru/` for her persona, cliché map, and every string she
sends you directly (voice/photo errors, status lines like "Reading
example.com…", the reminder/tracker delivery prefixes, the CLI backend's
quota-exhausted message). If you set `KAYA_LANGUAGE` to a code that isn't
fully set up, she refuses to boot and logs exactly which files are missing —
see `agents/README.md`'s "Multi-language support" section for why.

### Her locale files

```
agents/kaya/locales/<lang>/
    soul.md         her persona and voice rules — the biggest file, ~270-280
                      lines; this is what makes her sound like herself
    strings.json     everything her connector/delivery code says directly:
                      voice/photo errors, status lines, the crash message,
                      the reminder/tracker prefixes

agents/core/locales/<lang>/   (shared with any future agent)
    cliches.json     the AI-cliché map her self-check pass enforces
    strings.json     currently just the CLI backend's quota message
```

`ru` and `en` are both fully populated today. Adding a third language means
creating all four files above under the new code and translating them —
`agents/README.md` walks through it. The one file worth taking slowly is
`soul.md`: it's not just prose to translate line-for-line, it's tone
calibration (the "Voice" section's banned openers/closers are AI-slop
patterns *in that language*, not translations of the Russian or English
ones — what reads as a cliché in one language usually isn't the literal
translation of what reads as a cliché in another).

## Code layout

- `main.py` — boots her: the language completeness check, the DB, the Brain
  handshake, the LLM backend, the Telegram poller.
- `config.py` — her settings (`KayaSettings`, read from `.env`).
- `connector.py` — the Telegram side: incoming text/voice/photo handling,
  the owner-only gate, typing-status lines.
- `delivery.py` — the receiver Brain pushes events to (a fired reminder, a
  tracker notification) so she can message you on her own initiative.
- `strings.py` — the loader for her `locales/<lang>/strings.json`.
- `history.py` / `dedup.py` / `stt.py` — her local dialogue history,
  duplicate-update protection, and voice transcription (Yandex SpeechKit).
- `CHANGELOG.md` — every behavior-affecting change to her, dated, newest
  first. Update it in the same commit that makes the change.
