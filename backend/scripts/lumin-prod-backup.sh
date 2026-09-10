#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-backup.sh — Production backup v4.1
# Default: DRY-RUN (zero filesystem mutation).
# Execute: --execute
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

fatal_preflight() { echo "FATAL (preflight): $1"; exit 1; }
fail() { echo "FAIL: $1"; VALIDATION_ERRORS=$((VALIDATION_ERRORS + 1)); }

# ── DRY-RUN AWARE EXECUTION ──
# In dry-run: print only, never execute, never mutate.
x() {
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    echo "[EXEC] $*"
    "$@" || fatal_preflight "Command failed: $*"
  fi
}

echo "=== LUMIN PRODUCTION BACKUP V4.1 ==="
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "Release ID: $RELEASE_ID"
echo "Dry-run: $DRY_RUN"
echo ""

# ── VALIDATE ENVIRONMENT (preflight — no mutation yet) ──
[ "$(hostname)" = "$EXPECTED_HOSTNAME" ] || fatal_preflight "Hostname mismatch: expected $EXPECTED_HOSTNAME, got $(hostname)"
[ "$(whoami)" = "$EXPECTED_USER" ] || fatal_preflight "Must run as $EXPECTED_USER, got $(whoami)"
[ -d "$APP_DIR" ] || fatal_preflight "App directory not found: $APP_DIR"

BACKUP_DIR="$BACKUP_BASE/$RELEASE_ID"
echo "Backup dir: $BACKUP_DIR"

# ── DISK CHECK (uses nearest EXISTING path; dry-run safe) ──
echo "--- Disk Space ---"
DISK_CHECK_PATH="$BACKUP_BASE"
while [ ! -d "$DISK_CHECK_PATH" ] && [ "$DISK_CHECK_PATH" != "/" ]; do
  DISK_CHECK_PATH=$(dirname "$DISK_CHECK_PATH")
done
echo "Disk check path: $DISK_CHECK_PATH"
AVAIL_KB=$(df -k "$DISK_CHECK_PATH" 2>/dev/null | tail -1 | awk '{print $4}')
AVAIL_MB=$((AVAIL_KB / 1024))
echo "Available: ${AVAIL_MB}MB"
[ "$AVAIL_MB" -lt 500 ] && fatal_preflight "Insufficient disk: ${AVAIL_MB}MB < 500MB"

# ── CREATE BACKUP DIRECTORY (execute only) ──
if $DRY_RUN; then
  echo "[DRY-RUN] Would create parent: $BACKUP_BASE (mode 700)"
  echo "[DRY-RUN] Would create backup:  $BACKUP_DIR (mode 700)"
else
  [ -d "$BACKUP_BASE" ] || { mkdir "$BACKUP_BASE"; chmod 700 "$BACKUP_BASE"; }
  [ -d "$BACKUP_DIR" ] && fatal_preflight "Backup directory already exists: $BACKUP_DIR"
  mkdir "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
fi

# ── 1. APPLICATION ──
echo "--- Application ---"
x cp -a "$APP_DIR/backend" "$BACKUP_DIR/backend"
x cp -a "$APP_DIR/frontend" "$BACKUP_DIR/frontend"

# ── 2. ENVIRONMENT FILES (MANDATORY) ──
echo "--- Environment ---"
[ -f "$APP_DIR/backend/.env.lumin" ] || fatal_preflight "Mandatory file missing: backend/.env.lumin"
x cp "$APP_DIR/backend/.env.lumin" "$BACKUP_DIR/backend.env.lumin"
x chmod 600 "$BACKUP_DIR/backend.env.lumin"

[ -f "$APP_DIR/frontend/.env.local" ] || fatal_preflight "Mandatory file missing: frontend/.env.local"
x cp "$APP_DIR/frontend/.env.local" "$BACKUP_DIR/frontend.env.local"
x chmod 600 "$BACKUP_DIR/frontend.env.local"

# ── 3. SYSTEMD UNITS (MANDATORY) ──
echo "--- Systemd ---"
[ -f "/etc/systemd/system/faztrack-attendance-lumin.service" ] || fatal_preflight "Service file missing: backend unit"
x cp /etc/systemd/system/faztrack-attendance-lumin.service "$BACKUP_DIR/"
[ -f "/etc/systemd/system/faztrack-attendance-lumin-web.service" ] || fatal_preflight "Service file missing: frontend unit"
x cp /etc/systemd/system/faztrack-attendance-lumin-web.service "$BACKUP_DIR/"

# ── 4. CADDY CONFIG (MANDATORY) ──
echo "--- Caddy ---"
if $DRY_RUN; then
  echo "[DRY-RUN] Would dump Caddyfile from $CADDY_CONTAINER"
else
  docker exec "$CADDY_CONTAINER" cat /etc/caddy/Caddyfile > "$BACKUP_DIR/Caddyfile" || fatal_preflight "Caddy config dump failed"
  [ -s "$BACKUP_DIR/Caddyfile" ] || fatal_preflight "Caddy config empty"
fi

# ── 5. PERSISTENT DATA ──
echo "--- Persistent Data ---"
for dir in uploads runtime persistent; do
  for prefix in "$APP_DIR" "$APP_DIR/backend"; do
    if [ -d "$prefix/$dir" ]; then
      dest_name=$(basename "$prefix")-$dir
      x cp -a "$prefix/$dir" "$BACKUP_DIR/$dest_name"
      echo "  Backed up: $prefix/$dir -> $dest_name"
    fi
  done
done

# ── 6. DATABASE DUMP (MANDATORY) ──
echo "--- Database ---"
DB_DUMP="$BACKUP_DIR/db-dump-$RELEASE_ID.dump"
if $DRY_RUN; then
  echo "[DRY-RUN] Would run: docker exec $PG_CONTAINER pg_dump -U $PG_USER -d $PG_DB -Fc > $DB_DUMP"
  echo "[DRY-RUN] Would validate: pg_restore --list"
else
  docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" -Fc > "$DB_DUMP" || fatal_preflight "pg_dump failed"
  [ -f "$DB_DUMP" ] || fatal_preflight "Dump file not created"
  DUMP_SIZE=$(stat -c%s "$DB_DUMP" 2>/dev/null || echo 0)
  [ "$DUMP_SIZE" -gt 0 ] || fatal_preflight "Dump is empty (0 bytes)"
  echo "  Dump size: $(du -h "$DB_DUMP" | cut -f1)"
  docker exec -i "$PG_CONTAINER" pg_restore --list < "$DB_DUMP" > /dev/null 2>&1 || fatal_preflight "pg_restore --list failed"
  echo "  pg_restore --list: PASS"
fi

# ── 7. MANIFEST (generate BEFORE checksums so it is covered) ──
echo "--- Manifest ---"
if $DRY_RUN; then
  echo "[DRY-RUN] Would write BACKUP-MANIFEST.md (no secrets)"
else
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
- backend.env.lumin (mode 600)
- frontend.env.local (mode 600)
- faztrack-attendance-lumin.service
- faztrack-attendance-lumin-web.service
- Caddyfile
- db-dump-$RELEASE_ID.dump (pg_dump custom format)
- BACKUP-MANIFEST.md (this file, checksummed)
- checksums-sha256.txt

## Database Validation
- Dump exists: YES
- Dump size: $(du -h "$DB_DUMP" | cut -f1)
- pg_restore --list: PASS

## Summary
- Total files: (computed after checksums)
- Total size: (computed after checksums)
- No secrets displayed
EOF
fi

# ── 8. CHECKSUMS (INCLUDE manifest; relative paths) ──
echo "--- Checksums ---"
if $DRY_RUN; then
  echo "[DRY-RUN] Would write checksums-sha256.txt (includes BACKUP-MANIFEST.md)"
  echo "[DRY-RUN] Would run sha256sum -c (self-verify)"
else
  (
    cd "$BACKUP_DIR" || fatal_preflight "Cannot cd to backup dir"
    find . -type f -not -name "checksums-sha256.txt" -printf "%P\n" | sort > /tmp/.lumin-filelist.$$
    while IFS= read -r f; do sha256sum "$f"; done < /tmp/.lumin-filelist.$$ > checksums-sha256.txt
    rm -f /tmp/.lumin-filelist.$$
  ) || fatal_preflight "Checksum generation failed"

  # Self-verify (includes BACKUP-MANIFEST.md)
  ( cd "$BACKUP_DIR" && sha256sum -c checksums-sha256.txt > /dev/null 2>&1 ) || fatal_preflight "Checksum verification failed"
  echo "  SHA256 self-verify (incl. manifest): PASS"

  # Final verification result
  echo "PASS" > "$BACKUP_DIR/VERIFICATION-RESULT.txt"
  echo "checksums_verified: $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> "$BACKUP_DIR/VERIFICATION-RESULT.txt"
  echo "  VERIFICATION-RESULT.txt written"
fi

# ── 9. FINAL ──
echo ""
if [ "$VALIDATION_ERRORS" -gt 0 ]; then
  echo "=== BACKUP FAILED: $VALIDATION_ERRORS errors ==="
  exit 1
fi

if $DRY_RUN; then
  echo "=== DRY-RUN COMPLETE (no filesystem changes) ==="
else
  echo "=== BACKUP COMPLETE ==="
  echo "Location: $BACKUP_DIR"
  echo "Size: $(du -sh "$BACKUP_DIR" | cut -f1)"
  echo "Files: $(find "$BACKUP_DIR" -type f | wc -l)"
  echo "No services stopped or restarted."
fi
