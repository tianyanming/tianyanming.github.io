#!/usr/bin/env bash
# Wrapper: sync main android-pack → child folder jobs.
# Reads JENKINS_PASS / ALL_PROXY from env; optional /tmp/ss-proxy/env.sh.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -f /tmp/ss-proxy/env.sh ]]; then
  # shellcheck disable=SC1091
  source /tmp/ss-proxy/env.sh
fi

if [[ -z "${JENKINS_PASS:-}${JENKINS_TOKEN:-}" ]]; then
  echo "Set JENKINS_PASS (or JENKINS_TOKEN) first." >&2
  exit 1
fi

export JENKINS_URL="${JENKINS_URL:-https://jenkins.njyz999.com}"
export JENKINS_USER="${JENKINS_USER:-admin}"

exec python3 "$DIR/sync-android-pack-to-children.py" "$@"
