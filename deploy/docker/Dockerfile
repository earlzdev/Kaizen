# =============================================================================
# LearnBot Dockerfile
# =============================================================================
# Multi-stage is overkill here — we need the full runtime for
# sentence-transformers (torch, model weights). Single stage keeps it simple.
#
# The sentence-transformers model (~90 MB) is downloaded at build time so the
# container starts instantly without a network fetch.
# =============================================================================

# Node comes from the official image — used only to run the Claude Code CLI,
# which is how Kaizen talks to Claude on a Max subscription (no API key).
FROM node:22-slim AS node-bin

FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
# so logs appear in real time in `docker compose logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps needed on Linux:
#   libglib2.0-0 — pymupdf (PDF parsing) needs it at runtime
#   gcc — some Python packages compile C extensions during install
#   ca-certificates — HTTPS for outbound API calls
#   ffmpeg — splits long Telegram voice notes into <30s chunks for Yandex
#            SpeechKit's sync API (agents/kaya/stt.py)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libglib2.0-0 ca-certificates ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# --- Node.js + Claude Code CLI (Max-subscription backend) -------------------
# Copied from the official Node image rather than apt — smaller and pinned.
COPY --from=node-bin /usr/local/bin/node /usr/local/bin/
COPY --from=node-bin /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g @anthropic-ai/claude-code \
    && claude --version

# Install CPU-only PyTorch first (saves ~1.5 GB vs the default CUDA build).
# We don't need GPU support — the embedding model runs fine on CPU.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install the rest of the dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Headless Chromium for the browser tool (Yandex Maps routes/distances, JS
# sites). `--with-deps` pulls the system libraries Chromium needs on Debian.
# This adds ~400 MB and a couple of minutes to the build; that's the cost of a
# real browser that can render JavaScript pages.
RUN playwright install --with-deps chromium

# Pre-download the embedding model so it's baked into the image.
# Multilingual (understands Russian); keep this string in sync with
# MODEL_NAME in app/services/embedder.py.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

# Copy application code
COPY . .

# Default command. Every docker-compose service overrides `command:` with its
# own entrypoint (brain / agents.kaya / modules.*); this default just points at
# the central v2 service so a bare `docker run` of the image starts Brain.
CMD ["python", "-m", "brain.main"]
