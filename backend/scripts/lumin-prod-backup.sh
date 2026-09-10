#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-backup-v3.sh — Production backup (READ-ONLY on data)
# Default: DRY-RUN. Use --execute to actually run.
# Target: ubuntu@VM-8-230-ubuntu (43.163.7.128)
# ─────────────────────────────────────────────────────────
set -euo pipefail

# ── TARGET CONSTANTS ──
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

# ── VALIDATE ENVIRONMENT ──
echo "=== LUMIN PRODUCTION BACKUP V3 ==="
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Release ID: $RELEASE_ID"
echo "Dry-run: $DRY_RUN"
echo ""

# Hostname check — FATAL
if [ "$(hostname)" != "$EXPECTED_HOSTNAME" ]; then
  echo "FATAL: Hostname mismatch. Expected $EXPECTED_HOSTNAME, got $(hostname)"
  exit 1
fi

# User check — FATAL
if [ "$(whoami)" != "$EXPECTED_USER" ]; then
  echo "FATAL: Must run as $EXPECTED_USER, got $(whoami)"
  exit 1
fi

# App path check
if [ ! -d "$APP_DIR" ]; then
  echo "FATAL: App directory not found: $APP_DIR"
  exit 1
fi

# ── HELPERS ──
run() {
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    echo "[EXEC] $*"
    eval "$@"
  fi
}

fail() {
  echo "FATAL: $1"
  exit 1
}

# ── BACKUP DIRECTORY ──
BACKUP_DIR="$BACKUP_BASE/$RELEASE_ID"
echo "Backup dir: $BACKUP_DIR"

if $DRY_RUN; then
  echo "[DRY-RUN] Would create $BACKUP_DIR"
else
  # Ensure parent exists with secure permissions
  mkdir -p "$BACKUP_BASE"
  chmod 700 "$BACKUP_BASE"
  # Final directory must NOT exist
  if [ -d "$BACKUP_DIR" ]; then
    fail "Backup directory already exists: $BACKUP_DIR"
  fi
  mkdir "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
fi

# ── DISK SPACE CHECK ──
echo "--- Disk Space ---"
AVAIL_KB=$(df -k "$BACKUP_BASE" 2>/dev/null | tail -1 | awk '{print $4}' || echo 0)
AVAIL_MB=$((AVAIL_KB / 1024))
echo "Available: ${AVAIL_MB}MB"
if [ "$AVAIL_MB" -lt 500 ]; then
  fail "Insufficient disk space: ${AVAIL_MB}MB available, need at least 500MB"
fi

# ── 1. BACKUP APPLICATION ──
echo ""
echo "--- Application ---"
run "cp -a $APP_DIR/backend $BACKUP_DIR/backend" || fail "Backend copy failed"
run "cp -a $APP_DIR/frontend $BACKUP_DIR/frontend" || fail "Frontend copy failed"

# ── 2. BACKUP ENVIRONMENT FILES (mandatory) ──
echo ""
echo "--- Environment Files ---"
if [ ! -f "$APP_DIR/backend/.env.lumin" ]; then
  fail "Mandatory file missing: backend/.env.lumin"
fi
run "cp $APP_DIR/backend/.env.lumin $BACKUP_DIR/backend.env.lumin"
run "chmod 600 $BACKUP_DIR/backend.env.lumin"

if [ -f "$APP_DIR/frontend/.env.local" ]; then
  run "cp $APP_DIR/frontend/.env.local $BACKUP_DIR/frontend.env.local"
  run "chmod 600 $BACKUP_DIR/frontend.env.local"
else
  echo "  frontend/.env.local not found (optional)"
fi

# ── 3. BACKUP PERSISTENT DATA ──
echo ""
echo "--- Persistent Data ---"
for dir in uploads runtime persistent; do
  if [ -d "$APP_DIR/$dir" ]; then
    run "cp -a $APP_DIR/$dir $BACKUP_DIR/$dir"
    echo "  Backed up: $dir"
  fi
done

# ── 4. BACKUP SYSTEMD UNITS ──
echo ""
echo "--- Systemd ---"
for svc in faztrack-attendance-lumin.service faztrack-attendance-lumin-web.service; do
  src="/etc/systemd/system/$svc"
  if [ -f "$src" ]; then
    run "cp $src $BACKUP_DIR/$svc"
  else
    echo "  WARNING: $src not found"
  fi
done

# ── 5. BACKUP CADDY CONFIG ──
echo ""
echo "--- Caddy ---"
run "docker exec $CADDY_CONTAINER cat /etc/caddy/Caddyfile > $BACKUP_DIR/Caddyfile 2>/dev/null || echo 'Caddy config not available'"

# ── 6. BACKUP DATABASE ──
echo ""
echo "--- Database ---"
DB_DUMP="$BACKUP_DIR/db-dump-$RELEASE_ID.dump"
if $DRY_RUN; then
  echo "[DRY-RUN] docker exec $PG_CONTAINER pg_dump -U $PG_USER -d $PG_DB -Fc > $DB_DUMP"
else
  docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$DB_DUMP"
  # Validate dump
  if [ ! -f "$DB_DUMP" ]; then
    fail "Database dump file not created"
  fi
  DUMP_SIZE=$(stat -c%s "$DB_DUMP" 2>/dev/null || echo 0)
  if [ "$DUMP_SIZE" -eq 0 ]; then
    fail "Database dump is empty"
  fi
  echo "  Dump size: $(du -h "$DB_DUMP" | cut -f1)"
  
  # pg_restore --list validation
  echo "  Validating with pg_restore --list..."
  if ! docker exec -i "$PG_CONTAINER" pg_restore --list < "$DB_DUMP" > /dev/null 2>&1; then
    fail "pg_restore --list validation failed"
  fi
  TABLE_COUNT=$(docker exec -i "$PG_CONTAINER" pg_restore --list < "$DB_DUMP" 2>/dev/null | grep -c "TABLE " || echo 0)
  echo "  pg_restore --list: PASS ($TABLE_COUNT tables)"
fi

# ── 7. CHECKSUMS ──
echo ""
echo "--- Checksums ---"
if ! $DRY_RUN; then
  cd "$BACKUP_DIR"
  find . -type f -not -name "BACKUP-MANIFEST.md" -not -name "checksums-sha256.txt" | sort | xargs sha256sum > checksums-sha256.txt
  echo "  SHA256 checksums written"
  # Verify self
  if ! sha256sum -c checksums-sha256.txt > /dev/null 2>&1; then
    fail "SHA256 self-verification failed"
  fi
  echo "  SHA256 verification: PASS"
fi

# ── 8. BACKUP MANIFEST ──
echo ""
echo "--- Manifest ---"
if ! $DRY_RUN; then
  cat > "$BACKUP_DIR/BACKUP-MANIFEST.md" << MANIFEST
# BACKUP MANIFEST
Release ID: $RELEASE_ID
Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Hostname: $(hostname)
User: $(whoami)
App Dir: $APP_DIR

## Contents
- backend/ (full copy)
- frontend/ (full copy)
- backend.env.lumin (permissions 600)
- frontend.env.local (if exists, permissions 600)
- uploads/ (if exists)
- runtime/ (if exists)
- faztrack-attendance-lumin.service
- faztrack-attendance-lumin-web.service
- Caddyfile
- db-dump-$RELEASE_ID.dump (pg_dump custom format)
- checksums-sha256.txt

## Validation
- Dump exists: YES
- Dump size: $(du -h "$DB_DUMP" 2>/dev/null | cut -f1 || echo "N/A")
- pg_restore --list: $(docker exec -i "$PG_CONTAINER" pg_restore --list < "$DB_DUMP" > /dev/null 2>&1 && echo "PASS" || echo "FAIL")
- SHA256 checksums: PASS
- Total files: $(find "$BACKUP_DIR" -type f | wc -l)
- Total size: $(du -sh "$BACKUP_DIR" | cut -f1)

## Notes
- No services stopped or restarted
- No database schema changes
- No secrets in this manifest
MANIFEST
  echo "  BACKUP-MANIFEST.md created"
fi

# ── 9. FINAL SUMMARY ──
echo ""
echo "=== BACKUP SUMMARY ==="
if ! $DRY_RUN; then
  echo "Location: $BACKUP_DIR"
  echo "Total size: $(du -sh "$BACKUP_DIR" | cut -f1)"
  echo "Total files: $(find "$BACKUP_DIR" -type f | wc -l)"
  echo ""
  echo "--- Validation ---"
  echo "STATUS: PASS"
  echo "All validations passed."
  echo ""
  echo "No services were stopped or restarted."
else
  echo "[DRY-RUN] No files created."
fi
