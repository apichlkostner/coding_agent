set -a && source <path of python interpreter>/.env && set +a && firejail \
  --whitelist=<path of coding agent> \
  --whitelist=$PWD \
  --whitelist=<path of python interpreter> \
  --read-only=<path of coding agent> \
  --read-only=<path of python interpreter> \
  <path of python interpreter>/python3 -m agent