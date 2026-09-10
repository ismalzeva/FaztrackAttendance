#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-deploy.sh — Production deploy v4
# Default: DRY-RUN. Use --execute to actually run.
# Requires: --backup-dir <path> (exact backup directory)
# ─────────────────────────────────────────────────────────
set -euo pipefail

EXPECTED_HOSTNAME="VM-8-230-ubuntu"
EXPECTED_USER="ubuntu"
APP_DIR="/home/ubuntu/apps/attendance-lumin"
PG_CONTAINER="attendance-lumin-postgres"
EXPECTED_BUILD_ID="S0kC8_NAlhQyCLKMFHHdQ"
EXPECTED_ADMIN_CHUNK="page-7ef835f4a59d5f3e.js"
EXPECTED_DASH_CHUNK="page-18c48464202db5cb.js"
EXPECTED_ABSEN_CHUNK="page-b864c4195106e108.js"
RELEASE_ID="${RELEASE_ID:-$(date +%Y%m%d_%H%M%S)}"
DRY_RUN=true
BACKUP_DIR=""

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) DRY_RUN=false; shift ;;
    --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Absolute artifact paths
ARTIFACT_DIR="$(pwd -P)"
BACKEND_TAR="$ARTIFACT_DIR/lumin-backend-413720b1.tar.gz"
FRONTEND_TAR="$ARTIFACT_DIR/lumin-frontend-6e3a3e20.tar.gz"
BACKEND_SHA="b8b5d890f2e0d6dfdf225740e561b9c6b2ef6854f0416c96e283936ef334bba5"
FRONTEND_SHA="f96bf8805f19b555fb6f3509e57c3bc241f3cbeaa0a54b175a90f1fc71ec5740"

# State tracking
RELEASE_PAIR=""
FAILED_RELEASE=""
OLD_BACKEND=""
OLD_FRONTEND=""

fatal_after_switch() {
  echo ""
  echo "FATAL: $1"
  echo "Initiating paired rollback..."
  if [ -n "$OLD_BACKEND" ] && [ -d "$OLD_BACKEND" ] && [ -n "$OLD_FRONTEND" ] && [ -d "$OLD_FRONTEND" ]; then
    FAILED_RELEASE="$APP_DIR/releases/failed-$RELEASE_ID-$(date +%s)"
    mkdir -p "$FAILED_RELEASE"
    if [ -d "$APP_DIR/backend" ]; then mv "$APP_DIR/backend" "$FAILED_RELEASE/backend"; fi
    if [ -d "$APP_DIR/frontend" ]; then mv "$APP_DIR/frontend" "$FAILED_RELEASE/frontend"; fi
    mv "$OLD_BACKEND" "$APP_DIR/backend"
    mv "$OLD_FRONTEND" "$APP_DIR/frontend"
    sudo systemctl start faztrack-attendance-lumin.service 2>/dev/null || true
    sudo systemctl start faztrack-attendance-lumin-web.service 2>/dev/null || true
    echo "ROLLBACK COMPLETE"
    echo "Failed release: $FAILED_RELEASE"
  else
    echo "CANNOT ROLLBACK: Old release not found"
    echo "Manual recovery required."
  fi
  exit 1
}

run() {
  if $DRY_RUN; then echo "[DRY-RUN] $*"; else "$@" || fatal_after_switch "Command failed: $*"; fi
}

echo "=== LUMIN PRODUCTION DEPLOY V4 ==="
echo "Release ID: $RELEASE_ID"
echo "Dry-run: $DRY_RUN"
echo ""

# ── VALIDATE ENVIRONMENT ──
[ "$(hostname)" = "$EXPECTED_HOSTNAME" ] || fatal_after_switch "Hostname mismatch"
[ "$(whoami)" = "$EXPECTED_USER" ] || fatal_after_switch "Must run as $EXPECTED_USER"

# ── VALIDATE BACKUP ──
echo "--- Backup Validation ---"
[ -n "$BACKUP_DIR" ] || fatal_after_switch "Missing --backup-dir argument"
[ -d "$BACKUP_DIR" ] || fatal_after_switch "Backup directory not found: $BACKUP_DIR"
[ -f "$BACKUP_DIR/BACKUP-MANIFEST.md" ] || fatal_after_switch "BACKUP-MANIFEST.md not found"

# Check manifest hostname
BACKUP_HOST=$(grep "Hostname:" "$BACKUP_DIR/BACKUP-MANIFEST.md" | awk '{print $2}')
[ "$BACKUP_HOST" = "$(hostname)" ] || fatal_after_switch "Backup hostname mismatch: $BACKUP_HOST"

# Check manifest status
grep -q "Status: PASS" "$BACKUP_DIR/BACKUP-MANIFEST.md" || fatal_after_switch "Backup status not PASS"

# Verify checksums
if [ -f "$BACKUP_DIR/checksums-sha256.txt" ]; then
  if ! (cd "$BACKUP_DIR" && sha256sum -c checksums-sha256.txt > /dev/null 2>&1); then
    fatal_after_switch "Backup checksum failed"
  fi
  echo "  Checksums: PASS"
fi

# Exactly one dump
DUMPS=$(find "$BACKUP_DIR" -maxdepth 1 -name "db-dump-*.dump" 2>/dev/null | wc -l)
[ "$DUMPS" -eq 1 ] || fatal_after_switch "Expected 1 dump, found $DUMPS"
DUMP_FILE=$(find "$BACKUP_DIR" -maxdepth 1 -name "db-dump-*.dump" 2>/dev/null | head -1)
DUMP_SIZE=$(stat -c%s "$DUMP_FILE")
[ "$DUMP_SIZE" -gt 0 ] || fatal_after_switch "Dump is empty"
if ! docker exec -i "$PG_CONTAINER" pg_restore --list < "$DUMP_FILE" > /dev/null 2>&1; then
  fatal_after_switch "pg_restore --list failed"
fi
echo "  Backup validation: PASS"

# ── VERIFY ARTIFACTS ──
echo ""
echo "--- Artifact Verification ---"
[ -f "$BACKEND_TAR" ] || fatal_after_switch "Backend artifact not found: $BACKEND_TAR"
[ -f "$FRONTEND_TAR" ] || fatal_after_switch "Frontend artifact not found: $FRONTEND_TAR"
ACTUAL_BE=$(sha256sum "$BACKEND_TAR" | awk '{print $1}')
ACTUAL_FE=$(sha256sum "$FRONTEND_TAR" | awk '{print $1}')
[ "$ACTUAL_BE" = "$BACKEND_SHA" ] || fatal_after_switch "Backend SHA mismatch"
[ "$ACTUAL_FE" = "$FRONTEND_SHA" ] || fatal_after_switch "Frontend SHA mismatch"
echo "  Artifacts: PASS"

# ── STAGE RELEASE ──
echo ""
echo "--- Staging ---"
STAGING_DIR="$APP_DIR/staging-$RELEASE_ID"
[ -d "$STAGING_DIR" ] && fatal_after_switch "Staging dir already exists: $STAGING_DIR"
mkdir -p "$STAGING_DIR"

# Extract backend
echo "  Backend..."
mkdir -p "$STAGING_DIR/backend"
tar xzf "$BACKEND_TAR" -C "$STAGING_DIR/backend" --strip-components=1

# Copy venv (Option A)
echo "  Venv..."
cp -a "$APP_DIR/backend/.venv" "$STAGING_DIR/backend/.venv"

# Copy .env.lumin
cp "$APP_DIR/backend/.env.lumin" "$STAGING_DIR/backend/.env.lumin"

# Extract frontend
echo "  Frontend..."
mkdir -p "$STAGING_DIR/frontend"
tar xzf "$FRONTEND_TAR" -C "$STAGING_DIR/frontend"

# Copy .env.local
cp "$APP_DIR/frontend/.env.local" "$STAGING_DIR/frontend/.env.local"

# ── VALIDATE STAGED ──
echo ""
echo "--- Staged Validation ---"
[ -x "$STAGING_DIR/backend/.venv/bin/uvicorn" ] || fatal_after_switch "uvicorn not executable"
[ -f "$STAGING_DIR/backend/.env.lumin" ] || fatal_after_switch ".env.lumin missing"
[ -f "$STAGING_DIR/backend/app/main.py" ] || fatal_after_switch "main.py missing"
[ -f "$STAGING_DIR/frontend/.next/standalone/server.js" ] || fatal_after_switch "server.js missing"
[ -d "$STAGING_DIR/frontend/.next/static" ] || fatal_after_switch ".next/static missing"
STAGED_BUILD=$(cat "$STAGING_DIR/frontend/.next/BUILD_ID")
[ "$STAGED_BUILD" = "$EXPECTED_BUILD_ID" ] || fatal_after_switch "BUILD_ID mismatch: $STAGED_BUILD"
echo "  Staged: PASS"

# ── PRE-SWITCH HEALTH ──
echo ""
echo "--- Pre-switch Health ---"
curl -sf http://localhost:8011/health/live > /dev/null 2>&1 || fatal_after_switch "Backend unhealthy"
curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login | grep -q "200" || fatal_after_switch "Frontend unhealthy"
echo "  Health: PASS"

# ── PAIRED SWITCH ──
echo ""
echo "--- Paired Switch ---"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RELEASE_PAIR="$APP_DIR/releases/release-pair-$TIMESTAMP"
OLD_BACKEND="$RELEASE_PAIR/backend-old"
OLD_FRONTEND="$RELEASE_PAIR/frontend-old"
mkdir -p "$RELEASE_PAIR"

echo "  Stopping services..."
sudo systemctl stop faztrack-attendance-lumin.service
sudo systemctl stop faztrack-attendance-lumin-web.service

echo "  Moving current to release-pair..."
mv "$APP_DIR/backend" "$OLD_BACKEND"
mv "$APP_DIR/frontend" "$OLD_FRONTEND"

echo "  Activating staged..."
mv "$STAGING_DIR/backend" "$APP_DIR/backend"
mv "$STAGING_DIR/frontend" "$APP_DIR/frontend"


echo "  Starting services..."
sudo systemctl start faztrack-attendance-lumin.service
sudo systemctl start faztrack-attendance-lumin-web.service
sleep 3

# ── POST-DEPLOY VALIDATION ──
echo ""
echo "--- Post-deploy Validation ---"

# Systemd
BE_SVC=$(systemctl is-active faztrack-attendance-lumin.service)
FE_SVC=$(systemctl is-active faztrack-attendance-lumin-web.service)
[ "$BE_SVC" = "active" ] || fatal_after_switch "Backend service: $BE_SVC"
[ "$FE_SVC" = "active" ] || fatal_after_switch "Frontend service: $FE_SVC"
echo "  Systemd: PASS"

# Health
curl -sf http://localhost:8011/health/live > /dev/null 2>&1 || fatal_after_switch "Backend health failed"
curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login | grep -q "200" || fatal_after_switch "Frontend health failed"
echo "  Local health: PASS"

# Public
curl -sf -o /dev/null -w '%{http_code}' https://attendance-lumin.gofaztrack.com/login | grep -q "200" || fatal_after_switch "Public /login failed"
curl -sf -o /dev/null -w '%{http_code}' https://attendance-lumin.gofaztrack.com/absen | grep -q "200" || fatal_after_switch "Public /absen failed"
echo "  Public health: PASS"

# BUILD_ID
DEPLOYED_BUILD=$(cat "$APP_DIR/frontend/.next/BUILD_ID")
[ "$DEPLOYED_BUILD" = "$EXPECTED_BUILD_ID" ] || fatal_after_switch "BUILD_ID mismatch: $DEPLOYED_BUILD"
echo "  BUILD_ID: PASS"

# Chunk hashes
ADMIN_CHUNK=$(find "$APP_DIR/frontend/.next/static/chunks/app/admin" -maxdepth 1 -name "page-*.js" -printf "%f\n" 2>/dev/null | head -1)
DASH_CHUNK=$(find "$APP_DIR/frontend/.next/static/chunks/app/dashboard" -maxdepth 1 -name "page-*.js" -printf "%f\n" 2>/dev/null | head -1)
ABSEN_CHUNK=$(find "$APP_DIR/frontend/.next/static/chunks/app/absen" -maxdepth 1 -name "page-*.js" -printf "%f\n" 2>/dev/null | head -1)
[ "$ADMIN_CHUNK" = "$EXPECTED_ADMIN_CHUNK" ] || fatal_after_switch "Admin chunk mismatch: $ADMIN_CHUNK"
[ "$DASH_CHUNK" = "$EXPECTED_DASH_CHUNK" ] || fatal_after_switch "Dashboard chunk mismatch: $DASH_CHUNK"
[ "$ABSEN_CHUNK" = "$EXPECTED_ABSEN_CHUNK" ] || fatal_after_switch "Absen chunk mismatch: $ABSEN_CHUNK"
echo "  Chunk hashes: PASS"

# Persistent data
for dir in uploads runtime persistent; do
  for prefix in "$APP_DIR" "$APP_DIR/backend"; do
    if [ -d "$RELEASE_PAIR/backend-old/$dir" ] || [ -d "$RELEASE_PAIR/frontend-old/$dir" ]; then
      [ -d "$prefix/$dir" ] || echo "  WARNING: $prefix/$dir missing after switch"
    fi
  done
done

echo ""
echo "=== DEPLOY COMPLETE ==="
echo "Release: $RELEASE_ID"
echo "Release pair: $RELEASE_PAIR"
echo "Estimated downtime: ~10 seconds"
echo "Rollback: bash lumin-prod-rollback.sh --execute --release-pair $RELEASE_PAIR"
