#!/bin/sh
# Runs on the host before the container starts (devcontainer.json
# initializeCommand). Responsibilities:
#
#   1. Create the bind-mount sources, so Docker never creates them as
#      root-owned directories inside the container.
#   2. Seed them once from .devcontainer-state-export/ if present. That export
#      holds the VS Code user state and shell history captured from the
#      container before the mounts were introduced. The copy is skipped when
#      the destination already has data, so this stays idempotent across
#      rebuilds and later starts.
#
# Delete .devcontainer-state-export/ once a rebuild has been verified.

set -eu

state_dir="$HOME/.devcontainer-state/coding_agent"
user_dir="$state_dir/vscode-user"
history_dir="$state_dir/history"
export_dir="$1"

mkdir -p "$HOME/.pi/agent" "$user_dir" "$history_dir"

if [ -d "$export_dir/vscode-user" ] && [ ! -d "$user_dir/globalStorage" ]; then
    echo "persist-init: seeding VS Code user state into $user_dir"
    cp -a "$export_dir/vscode-user/." "$user_dir/"
fi

if [ -f "$export_dir/history/.bash_history" ] && [ ! -f "$history_dir/.bash_history" ]; then
    echo "persist-init: seeding shell history into $history_dir"
    cp -a "$export_dir/history/.bash_history" "$history_dir/.bash_history"
fi
