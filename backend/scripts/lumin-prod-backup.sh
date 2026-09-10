#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-backup.sh — Production backup v4
# Default: DRY-RUN. Use --execute to actually run.
# Target: ubuntu@VM-8-230-ubuntu (43.163.7.128)
# ─────────────────────────────────────────────────────────
set -euo pipefail

EXPECTED_HOSTNAME="VM-8-230-ubuntu"
EXPECTED_USER="ubuntu"
APP_DIR="/home/ubuntu/apps/attendance-lumin"
BACKUP_BASE="/home/ubuntu/backups/attendance-lumin"
PG_CONTAINER="attendance-lumin-postgres"
PG_USER="faztrack_lumin"
PG_DB="faztrack_attendance_lumin"
CADDY_CONTAINER="caddy-main"
RELEASE_ID="${RELEASE_ID:-$(date +%Y%m%d_%H%M%S)}"
DRY_RUN=true
[[ "${1:-}" == "--execute" ]] && DRY_RUN=false

VALIDATION_ERRORS=0

fail() { echo "FAIL: $1"; VALIDATION_ERRORS=$((VALIDATION_ERRORS + 1)); }
fatal() { echo "FATAL: $1"; exit 1; }

run() {
  if $DRY_RUN; then echo "[DRY-RUN] $*"; else "$@"; fi
}

echo "=== LUMIN PRODUCTION BACKUP V4 ==="
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Release ID: $RELEASE_ID"
echo "Dry-run: $DRY_RUN"
echo ""

# ── VALIDATE ENVIRONMENT ──
[ "$(hostname)" = "$EXPECTED_HOSTNAME" ] || fatal "Hostname mismatch: expected $EXPECTED_HOSTNAME, got $(hostname)"
[ "$(whoami)" = "$EXPECTED_USER" ] || fatal "Must run as $EXPECTED_USER"
[ -d "$APP_DIR" ] || fatal "App directory not found: $APP_DIR"

# ── BACKUP DIRECTORY ──
BACKUP_DIR="$BACKUP_BASE/$RELEASE_ID"
echo "Backup dir: $BACKUP_DIR"

if ! $DRY_RUN; then
  mkdir -p "$BACKUP_BASE"
  chmod 700 "$BACKUP_BASE"
  [ -d "$BACKUP_DIR" ] && fatal "Backup directory already exists: $BACKUP_DIR"
  mkdir "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
fi

# ── DISK SPACE ──
echo "--- Disk Space ---"
AVAIL_KB=$(df -k "$BACKUP_BASE" 2>/dev/null | tail -1 | awk '{print $4}')
AVAIL_MB=$((AVAIL_KB / 1024))
echo "Available: ${AVAIL_MB}MB"
[ "$AVAIL_MB" -lt 500 ] && fatal "Insufficient disk: ${AVAIL_MB}MB"

# ── 1. APPLICATION ──
echo "--- Application ---"
run cp -a "$APP_DIR/backend" "$BACKUP_DIR/backend"
run cp -a "$APP_DIR/frontend" "$BACKUP_DIR/frontend"

# ── 2. ENVIRONMENT FILES (MANDATORY) ──
echo "--- Environment ---"
[ -f "$APP_DIR/backend/.env.lumin" ] || fatal "Mandatory file missing: backend/.env.lumin"
run cp "$APP_DIR/backend/.env.lumin" "$BACKUP_DIR/backend.env.lumin"
run chmod 600 "$BACKUP_DIR/backend.env.lumin"

[ -f "$APP_DIR/frontend/.env.local" ] || fatal "Mandatory file missing: frontend/.env.local"
run cp "$APP_DIR/frontend/.env.local" "$BACKUP_DIR/frontend.env.local"
run chmod 600 "$BACKUP_DIR/frontend.env.local"

# ── 3. SYSTEMD UNITS (MANDATORY) ──
echo "--- Systemd ---"
[ -f "/etc/systemd/system/faztrack-attendance-lumin.service" ] || fatal "Service file missing"
run cp /etc/systemd/system/faztrack-attendance-lumin.service "$BACKUP_DIR/"
[ -f "/etc/systemd/system/faztrack-attendance-lumin-web.service" ] || fatal "Service file missing"
run cp /etc/systemd/system/faztrack-attendance-lumin-web.service "$BACKUP_DIR/"

# ── 4. CADDY CONFIG (MANDATORY) ──
echo "--- Caddy ---"
if ! $DRY_RUN; then
  docker exec "$CADDY_CONTAINER" cat /etc/caddy/Caddyfile > "$BACKUP_DIR/Caddyfile"
  [ -s "$BACKUP_DIR/Caddyfile" ] || fatal "Caddy config empty or unavailable"
fi

# ── 5. PERSISTENT DATA ──
echo "--- Persistent Data ---"
for dir in uploads runtime persistent; do
  for prefix in "$APP_DIR" "$APP_DIR/backend"; do
    if [ -d "$prefix/$dir" ]; then
      run cp -a "$prefix/$dir" "$BACKUP_DIR/$(basename $prefix)-$dir"
      echo "  Backed up: $prefix/$dir"
    fi
  done
done

# ── 6. DATABASE DUMP (MANDATORY) ──
echo "--- Database ---"
DB_DUMP="$BACKUP_DIR/db-dump-$RELEASE_ID.dump"
if ! $DRY_RUN; then
  docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$DB_DUMP"
  [ -f "$DB_DUMP" ] || fatal "Dump file not created"
  DUMP_SIZE=$(stat -c%s "$DB_DUMP" 2>/dev/null || echo 0)
  [ "$DUMP_SIZE" -gt 0 ] || fatal "Dump is empty"
  echo "  Dump size: $(du -h "$DB_DUMP" | cut -f1)"
  if ! docker exec -i "$PG_CONTAINER" pg_restore --list < "$DB_DUMP" > /dev/null 2>&1; then
    fatal "pg_restore --list failed"
  fi
  echo "  pg_restore --list: PASS"
fi

# ── 7. CHECKSUMS ──
echo "--- Checksums ---"
if ! $DRY_RUN; then
  cd "$BACKUP_DIR"
  find . -type f -not -name "checksums-sha256.txt" -not -name "BACKUP-MANIFEST.md" | sort | xargs sha256sum > checksums-sha256.txt
  sha256sum -c checksums-sha256.txt > /dev/null 2>&1 || fatal "Checksum verification failed"
  echo "  SHA256 verification: PASS"
fi

# ── 8. MANIFEST ──
echo "--- Manifest ---"
if ! $DRY_RUN; then
  cat > "$BACKUP_DIR/BACKUP-MANIFEST.md" << EOF
# BACKUP MANIFEST
Release ID: $RELEASE_ID
Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Hostname: $(hostname)
User: $(whoami)
App Dir: $APP_DIR

## Validation Status: PASS

## Contents
- backend/ (full copy)
- frontend/ (full copy)
- backend.env.lumin
- frontend.env.local
- faztrack-attendance-lumin.service
- faztrack-attendance-lumin-web.service
- Caddyfile
- db-dump-$RELEASE_ID.dump
- checksums-sha256.txt

## Database Validation
- Dump exists: YES
- Dump size: $(du -h "$DB_DUMP" | cut -f1)
- pg_restore --list: PASS

## Summary
- Total files: $(find "$BACKUP_DIR" -type f | wc -l)
- Total size: $(du -sh "$BACKUP_DIR" | cut -f1)
- No secrets displayed
EOF
fi

# ── FINAL ──
echo ""
if [ "$VALIDATION_ERRORS" -gt 0 ]; then
  echo "=== BACKUP FAILED: $VALIDATION_ERRORS errors ==="
  exit 1
fi

echo "=== BACKUP COMPLETE ==="
echo "Location: $BACKUP_DIR"
echo "No services stopped or restarted."
