#!/usr/bin/env bash
# Build and start the benchmark container. RUN THIS ON THE HOST, not inside the
# devcontainer (the devcontainer has no Docker access).
#
# Usage:
#   ./run_container.sh [--port <host-port>] [--network <name>] [--rebuild] [--stop]
#
# Options:
#   --port <n>      Host port to publish sshd on (default: 2222).
#   --network <n>   Docker network to join (default: dev). The container must
#                   share a network with the LLM endpoint (e.g. litellm) so the
#                   agent can reach it. The devcontainer uses the "dev" network.
#   --rebuild       Force a rebuild of the image before starting.
#   --stop          Stop and remove the running container, then exit.
#
# The container is named "coding-agent-bench" and stays up until stopped.
set -euo pipefail

DOCKER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DOCKER_DIR/../.." && pwd)"

IMAGE="coding-agent-bench"
CONTAINER="coding-agent-bench"
PORT=2222
NETWORK="dev"
REBUILD=0
STOP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --network) NETWORK="$2"; shift 2 ;;
    --rebuild) REBUILD=1; shift ;;
    --stop) STOP=1; shift ;;
    -*) echo "Unknown option: $1" >&2; exit 2 ;;
    *) echo "Unexpected argument: $1" >&2; exit 2 ;;
  esac
done

if [[ "$STOP" -eq 1 ]]; then
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  echo "Stopped $CONTAINER"
  exit 0
fi

# ---------------------------------------------------------------------------
# SSH keypair for the devcontainer -> container connection.
# ---------------------------------------------------------------------------
KEY="$DOCKER_DIR/id_ed25519"
if [[ ! -f "$KEY" ]]; then
  echo "==> Generating SSH keypair at $KEY"
  ssh-keygen -t ed25519 -N "" -C "coding-agent-bench" -f "$KEY"
fi
PUBKEY="$(cat "$KEY.pub")"

# ---------------------------------------------------------------------------
# Build.
# ---------------------------------------------------------------------------
if [[ "$REBUILD" -eq 1 ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "==> Building $IMAGE"
  docker build \
    -t "$IMAGE" \
    --build-arg "BENCH_AUTHORIZED_KEY=$PUBKEY" \
    -f "$DOCKER_DIR/Dockerfile" \
    "$REPO_ROOT"
fi

# ---------------------------------------------------------------------------
# Run.
# ---------------------------------------------------------------------------
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

ENV_ARGS=()
# Prefer a dedicated benchmark .env (scoped key) over the main repo .env, to
# match run_task.sh's fallback order.
ENV_FILE="$REPO_ROOT/benchmark/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  ENV_FILE="$REPO_ROOT/.env"
fi
if [[ -f "$ENV_FILE" ]]; then
  ENV_ARGS+=(--env-file "$ENV_FILE")
  echo "==> Injecting secrets from $ENV_FILE"
else
  echo "==> WARNING: no benchmark/.env or .env found; the agent will have no API key" >&2
fi

echo "==> Starting $CONTAINER on port $PORT (network: $NETWORK)"
docker run -d \
  --name "$CONTAINER" \
  --network "$NETWORK" \
  -p "${PORT}:22" \
  "${ENV_ARGS[@]}" \
  "$IMAGE" >/dev/null

echo
echo "Container is up. From the devcontainer:"
echo "  ssh -i benchmark/docker/id_ed25519 -p $PORT bench@host.docker.internal"
echo
echo "Stop it with:"
echo "  $DOCKER_DIR/run_container.sh --stop"
