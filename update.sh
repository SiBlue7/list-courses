#!/usr/bin/env bash
set -Eeuo pipefail

# Safe deploy helper for this project.
# Usage:
#   ./update.sh
# Optional env vars:
#   SKIP_PULL=1         Skip git pull step
#   NO_CACHE=1          Build web image without cache (default: 1)
#   PRUNE_BUILDER=1     Run docker builder prune -af before build
#   RESTART_TUNNEL=1    Restart cloudflared service after deploy (default: 1)

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

NO_CACHE="${NO_CACHE:-1}"
SKIP_PULL="${SKIP_PULL:-0}"
PRUNE_BUILDER="${PRUNE_BUILDER:-0}"
RESTART_TUNNEL="${RESTART_TUNNEL:-1}"

log() {
  printf '[%s] %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

run() {
  log "$*"
  "$@"
}

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  die "Neither 'docker compose' nor 'docker-compose' is available."
fi

if ! command -v git >/dev/null 2>&1; then
  die "git is required."
fi

if ! command -v docker >/dev/null 2>&1; then
  die "docker is required."
fi

if [ "$SKIP_PULL" != "1" ]; then
  if [ -n "$(git status --porcelain)" ]; then
    die "Working tree is not clean. Commit/stash first, or run with SKIP_PULL=1."
  fi

  CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  run git fetch origin "$CURRENT_BRANCH"
  run git pull --ff-only origin "$CURRENT_BRANCH"
else
  log "SKIP_PULL=1 -> skipping git pull."
fi

if [ "$PRUNE_BUILDER" = "1" ]; then
  run docker builder prune -af
fi

BUILD_ARGS=()
if [ "$NO_CACHE" = "1" ]; then
  BUILD_ARGS+=(--no-cache)
fi

run "${COMPOSE[@]}" build "${BUILD_ARGS[@]}" web
run "${COMPOSE[@]}" up -d --remove-orphans

# Web command already runs migrate/collectstatic, but keep explicit commands to
# ensure consistency if startup command changes later.
run "${COMPOSE[@]}" exec -T web python manage.py migrate --noinput
run "${COMPOSE[@]}" exec -T web python manage.py collectstatic --noinput
run "${COMPOSE[@]}" exec -T web python manage.py check

if [ "$RESTART_TUNNEL" = "1" ]; then
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q '^cloudflared\.service'; then
    run systemctl restart cloudflared
  else
    log "cloudflared service not found via systemctl, skipping tunnel restart."
  fi
else
  log "RESTART_TUNNEL=0 -> skipping cloudflared restart."
fi

run "${COMPOSE[@]}" ps
run "${COMPOSE[@]}" logs --tail=40 web nginx

log "Deploy complete."
