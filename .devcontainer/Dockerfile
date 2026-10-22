FROM mcr.microsoft.com/devcontainers/python:3.12-trixie

# ---------------------------------------------------------------------------
# Node.js LTS
# The devcontainers/python image ships nvm for the non-root "vscode" user.
# ---------------------------------------------------------------------------
ARG NODE_VERSION="lts/*"
RUN su vscode -c "source /usr/local/share/nvm/nvm.sh && nvm install ${NODE_VERSION} 2>&1"



# ---------------------------------------------------------------------------
# uv — fast Python package manager
# Copied directly from the official uv image so the version is pinned to
# whatever "latest" was at image build time and no curl/sh pipe is needed.
# ---------------------------------------------------------------------------
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ---------------------------------------------------------------------------
# additional installs
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y \
    clangd \
    clang-tidy \
    clang-format \
    ripgrep

# ---------------------------------------------------------------------------
# Bind-mount targets for persisted state (see devcontainer.json).
# Docker creates missing mount targets as root-owned directories, which would
# leave the VS Code server unable to write its own state. Create them here with
# the correct ownership instead.
# ---------------------------------------------------------------------------
RUN mkdir -p /home/vscode/.vscode-server/data/User /home/vscode/.history \
    && chown -R vscode:vscode /home/vscode/.vscode-server /home/vscode/.history

RUN su vscode -c " \
    source /usr/local/share/nvm/nvm.sh && \
    npm install -g @earendil-works/pi-coding-agent && \
    npm install -g @opencode/cli && \
    npm install -g pyright \
"
