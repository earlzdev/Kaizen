# =============================================================================
# youtube_transcript tool — tools/youtube_transcript/tool.py
# =============================================================================
# WHAT: Pulls a YouTube video's transcript so the agent can "watch" it by
#       reading. Wraps the ported YouTubeService (youtube.py).
#
# HOW: exports `TOOL`; the loader registers it.
# =============================================================================

from tools.contract import ToolDef
from tools.youtube_transcript.youtube import YouTubeService

_MAX_TEXT = 12_000

_yt = YouTubeService()


async def youtube_transcript(url: str, language: str = "en") -> str:
    text = await _yt.transcript(url, language)
    if not text:
        return "Error: couldn't get a transcript for that video (no captions or bad URL)."
    return text[:_MAX_TEXT]


TOOL = ToolDef(
    name="youtube_transcript",
    description=(
        "Get a YouTube video's transcript so you can 'watch' it by reading — for "
        "reviews, tutorials, talks the owner points you at. Pass the video URL or "
        "id, and `language` matching the language you're currently replying in — "
        "it's only a preference (falls back to whatever caption track the video "
        "actually has)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "language": {"type": "string", "enum": ["en", "ru"], "description": "Preferred caption language (default en)"},
        },
        "required": ["url"],
    },
    handler=youtube_transcript,
)
