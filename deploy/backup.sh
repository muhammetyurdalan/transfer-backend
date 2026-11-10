#!/bin/bash
# Nightly PostgreSQL dump, keeps the last 14 days.
# cron: 30 3 * * * /opt/transfer/backup.sh >> /opt/transfer/backups/backup.log 2>&1
set -euo pipefail
cd /opt/transfer
mkdir -p backups
file="backups/transfer-$(date +%F-%H%M).sql.gz"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$file"
find backups -name 'transfer-*.sql.gz' -mtime +14 -delete
echo "$(date '+%F %T') backup ok: $file"
