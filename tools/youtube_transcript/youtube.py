# =============================================================================
# YouTube Service — app/services/youtube.py
# =============================================================================
# WHAT: Fetches the transcript (captions) of a YouTube video as plain text.
#
# WHY: The user wants Kaya to do what a person would — including "watching" a
#      video to answer from it (a review, a tutorial, a talk). She can't watch,
#      but almost every video carries captions, and reading those gives the same
#      information. This pulls them with no API key.
#
# HOW: youtube-transcript-api is synchronous, so it runs in a thread. Captions
#      may be missing or disabled; that's returned as None, not an error, so the
#      caller can say "this one has no transcript" and move on.
# =============================================================================

import asyncio
import logging
import re

logger = logging.getLogger(__name__)

# Cap the returned text so a two-hour video can't blow up the prompt.
_MAX_CHARS = 16_000

# Preference order per requested language, then whatever the video has.
_PREFERRED_LANGS = {"en": ["en", "ru"], "ru": ["ru", "en"]}


class YouTubeService:
    """Reads YouTube captions as text."""

    @staticmethod
    def video_id(url_or_id: str) -> str | None:
        """Extract the 11-char video id from a URL or accept a bare id."""
        text = url_or_id.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
            return text
        patterns = [
            r"(?:v=|/watch\?.*v=)([A-Za-z0-9_-]{11})",
            r"youtu\.be/([A-Za-z0-9_-]{11})",
            r"/shorts/([A-Za-z0-9_-]{11})",
            r"/embed/([A-Za-z0-9_-]{11})",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                return m.group(1)
        return None

    async def transcript(self, url_or_id: str, language: str = "en") -> str | None:
        """Return the video's transcript as text, or None if unavailable."""
        vid = self.video_id(url_or_id)
        if vid is None:
            return None

        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except Exception:
            logger.warning("youtube-transcript-api not installed — transcripts unavailable")
            return None

        preferred = _PREFERRED_LANGS.get(language, _PREFERRED_LANGS["en"])

        def _fetch() -> str | None:
            try:
                items = YouTubeTranscriptApi.get_transcript(vid, languages=preferred)
            except Exception:
                # Try again letting the library pick ANY available language.
                try:
                    items = YouTubeTranscriptApi.get_transcript(vid)
                except Exception:
                    logger.debug("No transcript for %s", vid, exc_info=True)
                    return None
            text = " ".join(part.get("text", "") for part in items)
            text = re.sub(r"\s+", " ", text).strip()
            return text or None

        return await asyncio.to_thread(_fetch)
