# YourProject Agent Container
# Contains: Claude CLI + example backend toolchain + example mobile SDK + JDK 17 + gh CLI
# (this example targets a JVM-based mobile toolchain — swap for your own stack)
# Big image by design — includes full mobile toolchain for build verification

# ── Multi-stage: grab binaries without network in final image ────────────────
FROM node:22-slim AS node-bin
FROM golang:1.23-bookworm AS go-bin

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

# ── System packages ───────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    # Core
    curl wget git unzip zip ca-certificates gnupg \
    # Build tools
    build-essential pkg-config \
    # Python (for json escaping in scripts)
    python3 \
    # Network
    openssh-client \
    # JDK 17 — required for this example's mobile build toolchain
    openjdk-17-jdk \
    && rm -rf /var/lib/apt/lists/* \
    # Create arch-independent symlink so JAVA_HOME works on both amd64 and arm64
    && ln -sf "$(dirname $(dirname $(readlink -f $(which java))))" /usr/lib/jvm/java-17-openjdk-current

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-current

# ── Node.js 22 (copied from official image — no network needed) ─────────────
COPY --from=node-bin /usr/local/bin/node /usr/local/bin/
COPY --from=node-bin /usr/local/include/node /usr/local/include/node
COPY --from=node-bin /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -sf ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack

# ── Example backend toolchain (copied from official image — no network needed) ──
COPY --from=go-bin /usr/local/go /usr/local/go

ENV PATH="/usr/local/go/bin:${PATH}"
ENV GOPATH="/home/agent/go"
ENV PATH="${GOPATH}/bin:${PATH}"

# ── GitHub CLI (direct binary — no apt repo needed) ─────────────────────────
RUN GH_VERSION=2.67.0 \
    && ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${ARCH}.tar.gz" \
       -o /tmp/gh.tar.gz \
    && tar -xzf /tmp/gh.tar.gz -C /tmp \
    && mv /tmp/gh_${GH_VERSION}_linux_${ARCH}/bin/gh /usr/local/bin/gh \
    && rm -rf /tmp/gh.tar.gz /tmp/gh_*

# ── Android SDK ───────────────────────────────────────────────────────────────
ENV ANDROID_HOME=/opt/android-sdk
ENV ANDROID_SDK_ROOT=/opt/android-sdk
ENV PATH="${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${PATH}"

# Command-line tools (version 11076708 = 2024)
RUN mkdir -p ${ANDROID_HOME}/cmdline-tools \
    && wget -q https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip \
       -O /tmp/cmdtools.zip \
    && unzip -q /tmp/cmdtools.zip -d /tmp/cmdtools \
    && mv /tmp/cmdtools/cmdline-tools ${ANDROID_HOME}/cmdline-tools/latest \
    && rm -rf /tmp/cmdtools.zip /tmp/cmdtools

# Accept licenses by writing known-stable hash files (more reliable than piping yes)
RUN mkdir -p ${ANDROID_HOME}/licenses && \
    printf "24333f8a63b6825ea9c5514f83c2829b004d1fee\nd56f5187479451eabf01fb78af6dfcb131a6481e\n" \
        > ${ANDROID_HOME}/licenses/android-sdk-license && \
    printf "84831b9409646a918e30573bab4c9c91346d8abd\n" \
        > ${ANDROID_HOME}/licenses/android-sdk-preview-license && \
    printf "33b6837c92e468e58c5660e04c7905d5a757e1f7\n" \
        > ${ANDROID_HOME}/licenses/android-googletv-license

# Install SDK components
RUN sdkmanager --sdk_root=${ANDROID_HOME} \
        "platform-tools" \
        "platforms;android-35" \
        "build-tools;35.0.0"

# ── Claude CLI ────────────────────────────────────────────────────────────────
RUN npm install -g @anthropic-ai/claude-code

# ── Non-root user ─────────────────────────────────────────────────────────────
RUN useradd -m -s /bin/bash agent \
    # workspace volume will be owned by agent
    && mkdir -p /workspace && chown agent:agent /workspace \
    # claude-auth volume mounts here — must be agent-owned so tokens can be written
    && mkdir -p /home/agent/.claude && chown agent:agent /home/agent/.claude \
    # claude cli expects this directory to exist
    && mkdir -p /home/agent/.local/bin && chown agent:agent /home/agent/.local/bin \
    # gradle cache dir
    && mkdir -p /home/agent/.gradle && chown agent:agent /home/agent/.gradle \
    # android sdk readable by agent
    && chown -R agent:agent ${ANDROID_HOME}

# ── Entrypoint & agent runner ─────────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
COPY agent_runner.py /app/agent_runner.py
RUN chmod +x /entrypoint.sh

USER agent
WORKDIR /workspace
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
