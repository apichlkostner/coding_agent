# Shell history persisted across container rebuilds.
#
# Sourced from ~/.bashrc by the devcontainer postCreateCommand. The history file
# lives on a host bind mount (see .devcontainer/devcontainer.json), so it
# survives rebuilds.
#
# The base image sets HISTSIZE=1000 / HISTFILESIZE=2000, which is too small for
# day-to-day work. histappend plus `history -a` keeps concurrent VS Code
# terminals from overwriting each other's entries on exit.

export HISTFILE=/home/vscode/.history/.bash_history
export HISTSIZE=100000
export HISTFILESIZE=100000
export HISTCONTROL=ignoreboth

shopt -s histappend

case "$PROMPT_COMMAND" in
    *"history -a"*) ;;
    *) PROMPT_COMMAND="history -a${PROMPT_COMMAND:+; $PROMPT_COMMAND}" ;;
esac
