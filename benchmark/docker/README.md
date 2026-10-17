# Benchmark container

Runs benchmark tasks in a dedicated container so the coding agent is isolated
from the devcontainer. Docker runs on the **host**; the devcontainer drives the
container over SSH.

## Architecture

```
host
├── devcontainer        (VS Code, your shell)  -- ssh/scp/rsync -->  container
└── benchmark container (sshd + agent + toolchain, port 2222 -> 22)
```

## Host workflow

Run these on the host, from the repository root.

```bash
# Build (first time) and start the container.
./benchmark/docker/run_container.sh

# Rebuild after changing the Dockerfile.
./benchmark/docker/run_container.sh --rebuild

# Publish on a different port.
./benchmark/docker/run_container.sh --port 2223

# Join a different Docker network.
./benchmark/docker/run_container.sh --network my_network

# Stop and remove.
./benchmark/docker/run_container.sh --stop
```

The container is started with `--add-host=host.docker.internal:host-gateway`,
so host-relative endpoints in the injected `.env` (for example
`OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318`) resolve from
inside the container. Without this mapping, OpenTelemetry span exports fail
with a `NameResolutionError`.

## Devcontainer workflow

Run these inside the devcontainer.

```bash
# Run one task in the container.
./benchmark/run_task_remote.sh python-fix-bug

# Keep the remote working directory for inspection.
./benchmark/run_task_remote.sh python-fix-bug --keep

# Point at a non-default host/port.
# Note: the devcontainer is itself a container, so its `localhost` is not the
# host's `localhost`. Use `host.docker.internal` to reach the host's published
# port (default when the devcontainer maps it, as in this repo).
./benchmark/run_task_remote.sh python-fix-bug --host host.docker.internal

# Point at a remote host.
./benchmark/run_task_remote.sh python-fix-bug --host 192.168.1.10 --port 2223
```

Results are pulled back into `benchmark/results/`.

`run_task_remote.sh` runs the task and then grades it in two separate SSH
invocations. The grader is copied into the container only after the agent has
exited, so an agent that searches the home directory cannot find it.