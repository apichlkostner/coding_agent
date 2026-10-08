#!/usr/bin/env bash
# Entrypoint for the benchmark container: generate host keys if missing, then
# run sshd in the foreground.
set -euo pipefail

# Host keys are not baked into the image; generate them on first start.
ssh-keygen -A

# /run/sshd may be missing after a container restart.
mkdir -p /run/sshd

# ---------------------------------------------------------------------------
# Propagate the container's environment to SSH sessions.
#
# `docker run --env-file` sets variables in PID 1's environment, but sshd starts
# login shells with a clean environment, so those variables are not visible to
# the agent. Persist them to the bench user's ~/.ssh/environment, which sshd
# reads when PermitUserEnvironment is enabled (see sshd_config.d/bench.conf).
# ---------------------------------------------------------------------------
ENV_FILE="/home/bench/.ssh/environment"
: > "$ENV_FILE"
# Export every variable that is not part of the base container environment.
# `env -0` separates entries with NUL so values containing newlines survive.
while IFS= read -r -d '' entry; do
  name="${entry%%=*}"
  case "$name" in
    # Skip shell/sshd internals and the base image's own variables.
    HOME|PATH|PWD|OLDPWD|SHELL|TERM|USER|LOGNAME|HOSTNAME|SHLVL|_|LANG|LC_*|SSH_*|MAIL) continue ;;
  esac
  printf '%s\n' "$entry" >> "$ENV_FILE"
done < <(env -0)

chown bench:bench "$ENV_FILE"
chmod 600 "$ENV_FILE"

exec /usr/sbin/sshd -D -e
