#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-backup.sh — Backup production before deployment
# Default: DRY-RUN. Use --execute to actually run.
# ─────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="/home/ubuntu/apps/attendance-lumin"
PG_CONTAINER="attendance-lumin-postgres"
CADDY_CONTAINER="caddy-main"
BACKUP_BASE="$APP_DIR/backups"
TS=$(date +%Y%m%d_%H%M%S)
DRY_RUN=true

[[ "${1:-}" == "--execute" ]] && DRY_RUN=false

echo "=== LUMIN PRODUCTION BACKUP ==="
echo "Timestamp: $TS"
echo "Dry-run: $DRY_RUN"
echo "Target: $APP_DIR"
echo ""

run() {
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    echo "[EXEC] $*"
    eval "$@"
  fi
}

# Validate
if [ "$(hostname)" != "VM-3-20-ubuntu" ] && [ "$(hostname)" != "lumin-production" ]; then
  echo "WARNING: Unexpected hostname: $(hostname)"
  echo "Expected production server. Continuing anyway..."
fi

# Create backup directory
BACKUP_DIR="$BACKUP_BASE/$TS"
run "mkdir -p $BACKUP_DIR"

# 1. Backend
echo "--- Backend ---"
run "cp -a $APP_DIR/backend $BACKUP_DIR/backend"

# 2. Frontend
echo "--- Frontend ---"
run "cp -a $APP_DIR/frontend $BACKUP_DIR/frontend"

# 3. Environment files
echo "--- Environment ---"
if [ -f "$APP_DIR/backend/.env" ]; then
  run "cp $APP_DIR/backend/.env $BACKUP_DIR/backend.env"
  run "chmod 600 $BACKUP_DIR/backend.env"
fi
if [ -f "$APP_DIR/frontend/.env.local" ]; then
  run "cp $APP_DIR/frontend/.env.local $BACKUP_DIR/frontend.env.local"
  run "chmod 600 $BACKUP_DIR/frontend.env.local"
fi

# 4. Systemd units
echo "--- Systemd ---"
run "cp /etc/systemd/system/faztrack-attendance-lumin.service $BACKUP_DIR/ 2>/dev/null || true"
run "cp /etc/systemd/system/faztrack-attendance-lumin-web.service $BACKUP_DIR/ 2>/dev/null || true"

# 5. Caddy config
echo "--- Caddy ---"
run "docker exec $CADDY_CONTAINER cat /etc/caddy/Caddyfile > $BACKUP_DIR/Caddyfile 2>/dev/null || true"

# 6. Database dump
echo "--- Database ---"
run "docker exec $PG_CONTAINER pg_dump -U faztrack_lumin -d faztrack_attendance_lumin -Fc > $BACKUP_DIR/db-dump-$TS.dump"

# 7. Checksums
echo "--- Checksums ---"
if ! $DRY_RUN; then
  cd "$BACKUP_DIR"
  sha256sum backend/app/*.py > checksums-backend.txt 2>/dev/null
  sha256sum frontend/.next/BUILD_ID > checksums-frontend.txt 2>/dev/null
  sha256sum db-dump-*.dump > checksums-db.txt 2>/dev/null
  echo "Checksums written"
fi

# 8. Verify
echo "--- Verify ---"
if ! $DRY_RUN; then
  echo "Backup dir: $BACKUP_DIR"
  echo "Size: $(du -sh $BACKUP_DIR | cut -f1)"
  echo "Files: $(find $BACKUP_DIR -type f | wc -l)"
fi

echo ""
echo "=== BACKUP COMPLETE ==="
echo "Location: $BACKUP_DIR"
