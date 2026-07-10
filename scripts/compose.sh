#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if docker compose version >/dev/null 2>&1; then
  exec docker compose "$@"
fi

if command -v docker-compose >/dev/null 2>&1; then
  exec docker-compose "$@"
fi

case "${1:-}" in
  up)
    if docker inspect trust-runtime-postgres >/dev/null 2>&1; then
      exec docker start trust-runtime-postgres
    fi
    exec docker run \
      --name trust-runtime-postgres \
      --detach \
      --publish 5432:5432 \
      --env POSTGRES_DB=trust_runtime \
      --env POSTGRES_USER=trust \
      --env POSTGRES_PASSWORD=trust \
      --volume trust-runtime-trust-postgres:/var/lib/postgresql/data \
      --volume "$ROOT_DIR/infra/postgres/init.sql:/docker-entrypoint-initdb.d/001-init.sql:ro" \
      postgres:16-alpine
    ;;
  down)
    if docker inspect trust-runtime-postgres >/dev/null 2>&1; then
      docker stop trust-runtime-postgres >/dev/null
      docker rm trust-runtime-postgres >/dev/null
    fi
    exit 0
    ;;
  *)
    echo "Docker Compose is unavailable; fallback supports only 'up -d postgres' and 'down'." >&2
    exit 1
    ;;
esac
