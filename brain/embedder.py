# =============================================================================
# Brain embedder — brain/embedder.py
# =============================================================================
# WHAT: Turns text into a 384-dim vector for pgvector cosine search over Brain's
#       `facts`/`episodes`. A thin wrapper over sentence-transformers whose
#       encode runs OFF the event loop (asyncio.to_thread).
#
# WHY async embed (Step 6 of ARCHITECTURE_REVIEW.md): model.encode() is
#       synchronous CPU work; called directly from async handlers it froze the
#       ENTIRE process — the MCP server and the reminder sweeper included — for
#       every embed, and for the multi-second model load on the first call.
#       Now the encode runs in a worker thread and the loop keeps serving.
#       Encodes are serialized with an asyncio.Lock: torch forward passes from
#       concurrent threads are not a documented-safe pattern, and on this
#       CPU-only box parallel encodes would fight for the same cores anyway.
#
# WHY warmup() at boot: loading ~120 MB on the first owner message was a hidden
#       multi-second stall (the comment in main.py even believed loading
#       happened at boot — it didn't). warmup() also ASSERTS the model's real
#       dimension against EMBED_DIM: the 384 contract lives in the DB schema,
#       and a silently mismatched model would poison every stored vector.
#
# WHY multilingual: the owner writes in Russian and English; this model puts
#       "я из Уфы" and a later Russian question near each other. Same model as
#       the mentor's, same vector space.
#
# HOW: `await Embedder().embed("text") -> list[float]`; `await warmup()` once
#      at boot (brain/main.py).
# =============================================================================

import asyncio
import logging

from brain.db.models import EMBED_DIM

logger = logging.getLogger(__name__)

# Must match brain.db.models.EMBED_DIM / Vector(384) (same vector space as the
# mentor). Changing the model requires re-embedding every stored row.
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


class Embedder:
    """Text -> 384-dim vector via sentence-transformers, off the event loop."""

    def __init__(self) -> None:
        self._model = None
        # Serializes load + encode calls across the worker threads.
        self._lock = asyncio.Lock()

    def _ensure_loaded(self):
        """Load the model (sync — runs inside a worker thread)."""
        if self._model is None:
            # Imported lazily so importing this module (e.g. in tests) doesn't
            # drag in torch/sentence-transformers unless embeddings are used.
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model: %s", MODEL_NAME)
            self._model = SentenceTransformer(MODEL_NAME)
            dim = self._model.get_sentence_embedding_dimension()
            logger.info("Embedding model loaded (dim=%d)", dim)
            if dim != EMBED_DIM:
                # Fail fast: vectors of the wrong size would poison the tables.
                raise RuntimeError(
                    f"Embedding model '{MODEL_NAME}' has dim={dim}, "
                    f"but the schema requires EMBED_DIM={EMBED_DIM}"
                )
        return self._model

    async def warmup(self) -> None:
        """Load the model NOW (boot), not on the first owner message."""
        async with self._lock:
            await asyncio.to_thread(self._ensure_loaded)

    def _encode(self, text: str) -> list[float]:
        return self._ensure_loaded().encode(text, normalize_embeddings=True).tolist()

    async def embed(self, text: str) -> list[float]:
        """Convert a single text into a 384-dim vector (normalized, so cosine
        similarity equals dot product). Runs in a worker thread."""
        async with self._lock:
            return await asyncio.to_thread(self._encode, text)
