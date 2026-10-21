#!/bin/bash
# Cloud-session setup so the test suite runs like it does on a dev machine:
# tests start their own MariaDB via testcontainers (conftest.py pins the image
# to mariadb:latest, same as production's docker-compose), so all this needs
# is a running Docker daemon plus the Python deps.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# The cloud container ships dockerd but doesn't start it.
if ! docker info >/dev/null 2>&1; then
  nohup dockerd >/tmp/dockerd.log 2>&1 &
  for _ in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
fi

# Pull the test image now so the first pytest run doesn't wait on it. Docker
# Hub sometimes answers 429 for anonymous pulls, so retry; pytest pulls again
# on demand if this still fails.
for delay in 2 4 8 16; do
  docker pull -q mariadb:latest >/dev/null 2>&1 && break
  sleep "$delay"
done

# Headers needed to build the `mariadb` Python connector.
if ! dpkg -s libmariadb-dev >/dev/null 2>&1; then
  apt-get update -q >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends libmariadb-dev >/dev/null
fi

[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check -r requirements.txt pytest
