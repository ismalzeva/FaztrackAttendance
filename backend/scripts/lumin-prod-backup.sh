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
VALIDATION_OK=true

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

fail() {
  echo "ERROR: $1"
  VALIDATION_OK=false
}

# Validate hostname
if [ "$(hostname)" != "VM-3-20-ubuntu" ] && [ "$(hostname)" != "lumin-production" ]; then
  echo "WARNING: Unexpected hostname: $(hostname)"
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
DB_DUMP="$BACKUP_DIR/db-dump-$TS.dump"
run "docker exec $PG_CONTAINER pg_dump -U faztrack_lumin -d faztrack_attendance_lumin -Fc > $DB_DUMP"

# 7. Validate database dump
echo "--- Validate Database Dump ---"
if ! $DRY_RUN; then
  # Check file exists
  if [ ! -f "$DB_DUMP" ]; then
    fail "Database dump file not found: $DB_DUMP"
  else
    # Check file size > 0
    DUMP_SIZE=$(stat -c%s "$DB_DUMP" 2>/dev/null || echo 0)
    if [ "$DUMP_SIZE" -eq 0 ]; then
      fail "Database dump is empty (0 bytes)"
    else
      echo "  Dump size: $(du -h "$DB_DUMP" | cut -f1)"
      
      # pg_restore --list validation
      echo "  Validating with pg_restore --list..."
      if docker exec -i "$PG_CONTAINER" pg_restore --list < "$DB_DUMP" > /dev/null 2>&1; then
        TABLE_COUNT=$(docker exec -i "$PG_CONTAINER" pg_restore --list < "$DB_DUMP" 2>/dev/null | grep -c "TABLE " || echo 0)
        echo "  pg_restore --list: OK ($TABLE_COUNT tables)"
      else
        fail "pg_restore --list validation failed"
      fi
    fi
  fi
fi

# 8. Checksums
echo "--- Checksums ---"
if ! $DRY_RUN; then
  cd "$BACKUP_DIR"
  # SHA256 for entire backup
  find "$BACKUP_DIR" -type f -not -name "BACKUP-MANIFEST.md" -exec sha256sum {} \; > checksums-sha256.txt 2>/dev/null
  echo "  SHA256 checksums written"
fi

# 9. Create BACKUP-MANIFEST.md
echo "--- Manifest ---"
if ! $DRY_RUN; then
  cat > "$BACKUP_DIR/BACKUP-MANIFEST.md" << MANIFEST
# BACKUP MANIFEST
Timestamp: $TS
Hostname: $(hostname)
Backup Dir: $BACKUP_DIR

## Contents
- backend/ (full copy)
- frontend/ (full copy)
- backend.env (if exists, permissions 600)
- frontend.env.local (if exists, permissions 600)
- faztrack-attendance-lumin.service
- faztrack-attendance-lumin-web.service
- Caddyfile
- db-dump-$TS.dump (pg_dump custom format)
- checksums-sha256.txt

## Validation
- Dump file exists: $([ -f "$DB_DUMP" ] && echo "YES" || echo "NO")
- Dump size: $(du -h "$DB_DUMP" 2>/dev/null | cut -f1 || echo "N/A")
- pg_restore --list: $(docker exec -i "$PG_CONTAINER" pg_restore --list < "$DB_DUMP" > /dev/null 2>&1 && echo "PASS" || echo "FAIL")
- Total files: $(find "$BACKUP_DIR" -type f | wc -l)
- Total size: $(du -sh "$BACKUP_DIR" | cut -f1)

## Notes
- No services stopped or restarted during backup
- No database schema changes
- No secrets displayed in this manifest
MANIFEST
  echo "  BACKUP-MANIFEST.md created"
fi

# 10. Final summary
echo ""
echo "=== BACKUP SUMMARY ==="
if ! $DRY_RUN; then
  echo "Location: $BACKUP_DIR"
  echo "Total size: $(du -sh "$BACKUP_DIR" | cut -f1)"
  echo "Total files: $(find "$BACKUP_DIR" -type f | wc -l)"
  echo ""
  echo "--- Validation Results ---"
  if $VALIDATION_OK; then
    echo "STATUS: PASS"
    echo "All validations passed."
  else
    echo "STATUS: FAIL"
    echo "One or more validations failed. Check errors above."
    exit 1
  fi
else
  echo "[DRY-RUN] No files created."
fi

echo ""
echo "=== BACKUP COMPLETE ==="
echo "No services were stopped or restarted."
