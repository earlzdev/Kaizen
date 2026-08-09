# =============================================================================
# Conversation memory — infra/wardenkit/conversation.py
# =============================================================================
# WHAT: A bounded, on-disk window of the owner's recent exchanges with a
#       project, replayed into the prompt so a persona is not starting from
#       zero every time it is asked something.
#
# WHY it is bounded and NOT `claude --resume`: resuming a session carries the
#       entire history into every request and grows without bound — on the
#       subscription, which is already the binding constraint on how often the
#       fleet can run at all. A window of N exchanges costs a predictable
#       amount forever. The trade is real and deliberate: the persona forgets
#       what fell off the end, which is why the DURABLE half of memory is a
#       decision journal committed in the repo (the fleet's rulebooks describe
#       it) and this is only the short-term half. Keep the two separate — a
#       journal that fills up with small talk stops being read, and a
#       conversation window that tries to be permanent becomes a session.
#
# WHY it lives in the STATE volume and not in the repo: conversation is not a
#       project artifact. Committing every question the owner ever typed puts it
#       in the PR history, in every clone, and in front of anyone the repo is
#       later shared with.
#
# HOW:  log = ConversationLog("/state/conversation.jsonl")
#       prompt = log.render() + "\n\n=== Вопрос ===\n" + question
#       ... run ...
#       log.append(question, run.answer)
# =============================================================================

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Enough to hold a working session's worth of context and small enough that the
# replayed transcript never dominates the prompt it is attached to.
_MAX_TURNS = 30
# Answers are clipped, questions are not: the owner's own words are short and
# load-bearing, while a persona's answer can run to pages that add nothing on
# replay beyond what its first paragraph already said.
_MAX_ANSWER_CHARS = 1200


class ConversationLog:
    """The last N (question, answer) pairs, persisted as JSON lines.

    JSONL rather than one JSON document: an append is a single `open("a")`, and
    a file truncated by a container killed mid-write loses one line instead of
    the whole history.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_turns: int = _MAX_TURNS,
        max_answer_chars: int = _MAX_ANSWER_CHARS,
    ) -> None:
        self._path = Path(path)
        self._max_turns = max(1, max_turns)
        self._max_answer_chars = max(1, max_answer_chars)

    def load(self) -> list[dict]:
        """The window, oldest first. A missing or damaged file is an empty
        history, never an exception: memory is a nicety, and a project that
        refuses to answer because its log is corrupt has made things worse."""
        if not self._path.exists():
            return []
        turns: list[dict] = []
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    turn = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a half-written last line — skip it, keep the rest
                if isinstance(turn, dict) and turn.get("q"):
                    turns.append(turn)
        except OSError as e:
            logger.warning("conversation log unreadable (%s) — continuing without it", e)
            return []
        return turns[-self._max_turns:]

    def append(self, question: str, answer: str) -> None:
        """Record one exchange and trim the file back to the window.

        Rewriting the whole file on every append is fine at this size (tens of
        short lines) and it means the window is enforced on disk rather than
        only on read — otherwise the file grows forever and only LOOKS bounded.
        """
        question = (question or "").strip()
        if not question:
            return
        answer = (answer or "").strip()
        if len(answer) > self._max_answer_chars:
            answer = answer[: self._max_answer_chars].rstrip() + " […]"
        turns = self.load() + [{"q": question, "a": answer}]
        turns = turns[-self._max_turns:]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(
                "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in turns),
                encoding="utf-8",
            )
            # Atomic: a container killed mid-write leaves the previous window
            # intact rather than a truncated file.
            tmp.replace(self._path)
        except OSError as e:
            logger.warning("could not write conversation log (%s)", e)

    def render(self, *, header: str = "=== Предыдущие вопросы владельца ===") -> str:
        """The window as a transcript to paste into a prompt. Empty when there
        is no history — so a caller can concatenate unconditionally."""
        turns = self.load()
        if not turns:
            return ""
        lines = [header]
        for turn in turns:
            lines.append(f"Владелец: {turn.get('q', '')}")
            answer = turn.get("a", "")
            if answer:
                lines.append(f"Ты ответил: {answer}")
        return "\n".join(lines)


__all__ = ["ConversationLog"]
