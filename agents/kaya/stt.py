# =============================================================================
# Кая speech-to-text — agents/kaya/stt.py
# =============================================================================
# WHAT: Transcribes the owner's Telegram voice messages via Yandex SpeechKit
#       (short-audio sync API). Telegram voice is OGG/Opus — exactly what
#       SpeechKit's `oggopus` format expects, so no transcoding is needed.
#
# WHY Yandex SpeechKit (owner's choice): he already has tokens + quota there;
#       cloud STT keeps the image slim (no local whisper weights). Trade-off:
#       the audio leaves the container — acceptable per the owner.
#
# WHY chunking (2026-07-29, owner wanted 1-minute voice notes): the sync v1
#       API is hard-capped by Yandex at ~30s / 1 MB per request — that is THEIR
#       limit, not ours. Instead of the async long-audio API (S3 upload + job
#       polling), a longer note is split into <30s segments with ffmpeg, each
#       recognized with the same one-POST call, and the texts joined. Loses a
#       word at a cut boundary occasionally; acceptable for voice notes.
#
# HOW: `text = await SpeechKit(api_key).transcribe(ogg_bytes, duration_s)`
#       -> str | None (None = recognition failed; empty = recognized silence).
# =============================================================================

import asyncio
import glob
import logging
import os
import tempfile

import aiohttp

logger = logging.getLogger(__name__)

_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
# Yandex SpeechKit's language codes for the two supported KAYA_LANGUAGE
# values. Falls back to Russian for anything else (SpeechKit.__init__'s own
# default, kept as one source of truth via LANG_FOR_KAYA_LANGUAGE.get(...)).
LANG_FOR_KAYA_LANGUAGE = {"ru": "ru-RU", "en": "en-US"}
# Sync API hard limits (Yandex): ~1 MB body, ~30 s of audio PER REQUEST.
MAX_BYTES = 1024 * 1024
_MAX_SINGLE_SECONDS = 30
# What Кая accepts overall (chunked). ~60s of Telegram opus is ~250 KB, so the
# practical bound is duration, not size.
MAX_SECONDS = 60
# Segment length for the ffmpeg split — safely under the 30s API ceiling.
_CHUNK_SECONDS = 25


class SpeechKit:
    """Thin client for SpeechKit short-audio recognition."""

    def __init__(self, api_key: str, *, lang: str = "ru-RU", timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._lang = lang
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def transcribe(self, ogg_bytes: bytes, duration_s: int = 0) -> str | None:
        """OGG/Opus bytes -> recognized text. None on failure (logged).
        Notes longer than the sync API's 30s are split and recognized in
        chunks (see the header)."""
        if not self._api_key:
            return None
        if duration_s <= _MAX_SINGLE_SECONDS and len(ogg_bytes) <= MAX_BYTES:
            return await self._recognize(ogg_bytes)
        chunks = await self._split(ogg_bytes)
        if not chunks:
            return None
        # Chunks are independent — recognize them concurrently (gather keeps
        # order). One failed chunk = a hole mid-sentence; fail honestly.
        texts = await asyncio.gather(*(self._recognize(c) for c in chunks))
        if any(t is None for t in texts):
            return None
        return " ".join(t for t in texts if t).strip()

    async def _recognize(self, ogg_bytes: bytes) -> str | None:
        """One sync-API call (<=30s of audio)."""
        params = {"lang": self._lang, "format": "oggopus", "topic": "general"}
        headers = {"Authorization": f"Api-Key {self._api_key}"}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(
                    _STT_URL, params=params, headers=headers, data=ogg_bytes
                ) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status != 200:
                        logger.error("SpeechKit error %s: %s", resp.status, str(body)[:300])
                        return None
                    return (body.get("result") or "").strip()
        except aiohttp.ClientError as e:
            logger.error("SpeechKit unreachable: %s", e)
            return None

    @staticmethod
    async def _split(ogg_bytes: bytes) -> list[bytes] | None:
        """Split OGG/Opus into <30s segments via ffmpeg (re-encoded so every
        segment is a valid standalone oggopus file). None if ffmpeg failed."""
        with tempfile.TemporaryDirectory(prefix="stt-") as tmp:
            src = os.path.join(tmp, "in.ogg")
            with open(src, "wb") as f:
                f.write(ogg_bytes)
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", src,
                "-f", "segment", "-segment_time", str(_CHUNK_SECONDS),
                "-c:a", "libopus", "-b:a", "48k",
                os.path.join(tmp, "out%03d.ogg"),
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stderr=asyncio.subprocess.PIPE
                )
                _, err = await proc.communicate()
            except FileNotFoundError:
                logger.error("ffmpeg not found — can't split a long voice note")
                return None
            if proc.returncode != 0:
                logger.error("ffmpeg split failed: %s", err.decode(errors="replace")[:300])
                return None
            chunks = []
            for path in sorted(glob.glob(os.path.join(tmp, "out*.ogg"))):
                with open(path, "rb") as f:
                    chunks.append(f.read())
            return chunks or None
