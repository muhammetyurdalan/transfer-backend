#!/bin/bash
# Manual deploy on the server, without GitHub Actions.
# Pulls the main branch, builds the image here and restarts the service.
#
#   ./deploy.sh backend | microservice | frontend | all
set -euo pipefail

ROOT=/opt/transfer
SRC=$ROOT/src
OWNER=muhammetyurdalan
cd "$ROOT"

update_repo() {
  local repo=$1 dir=$SRC/$1
  if [ ! -d "$dir/.git" ]; then
    git clone --depth 1 --branch main "https://github.com/$OWNER/$repo.git" "$dir"
  else
    git -C "$dir" fetch --depth 1 origin main
    git -C "$dir" reset --hard origin/main
  fi
  echo ">> $repo @ $(git -C "$dir" log -1 --format='%h %s')"
}

image() { echo "ghcr.io/$OWNER/$1:latest"; }

deploy_backend() {
  update_repo transfer-backend
  docker build -t "$(image transfer-backend)" "$SRC/transfer-backend"
  # Server config is versioned in the backend repo
  mkdir -p caddy
  cp "$SRC/transfer-backend/deploy/docker-compose.yml" docker-compose.yml
  cp "$SRC/transfer-backend/deploy/caddy/Caddyfile" caddy/Caddyfile
  cp "$SRC/transfer-backend/deploy/backup.sh" backup.sh
  chmod +x backup.sh
  # Replace this script via rename so the running copy isn't modified mid-run
  install -m 755 "$SRC/transfer-backend/deploy/deploy.sh" deploy.sh.new
  mv deploy.sh.new deploy.sh
  docker compose up -d postgres redis caddy backend celery-worker celery-beat
  docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile
}

deploy_microservice() {
  update_repo transfer-microservice
  docker build -t "$(image transfer-microservice)" "$SRC/transfer-microservice"
  docker compose up -d microservice
}

deploy_frontend() {
  update_repo transfer-frontend
  [ -f "$ROOT/env/frontend.env" ] || { echo "Missing $ROOT/env/frontend.env"; exit 1; }
  docker build -t "$(image transfer-frontend)" \
    --secret id=frontend_env,src="$ROOT/env/frontend.env" \
    "$SRC/transfer-frontend"
  docker compose up -d frontend
}

mkdir -p "$SRC"
case "${1:-}" in
  backend)      deploy_backend ;;
  microservice) deploy_microservice ;;
  frontend)     deploy_frontend ;;
  all)          deploy_backend; deploy_microservice; deploy_frontend ;;
  *) echo "Usage: $0 backend|microservice|frontend|all"; exit 1 ;;
esac

docker image prune -f >/dev/null
docker compose ps
