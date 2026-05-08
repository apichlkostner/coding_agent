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
# pi coding agent — installed globally under the vscode user's active Node
# ---------------------------------------------------------------------------
RUN su vscode -c " \
    source /usr/local/share/nvm/nvm.sh && \
    npm install -g @earendil-works/pi-coding-agent \
"
