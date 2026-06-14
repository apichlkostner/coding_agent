firejail \
  --whitelist=./.test_project \
  --read-only=/workspaces/coding_agent/.venv/bin/python \
  --read-only=/workspaces/coding_agent \
  uv run agent .test_project