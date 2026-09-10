#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-deploy-v3.sh — Production deployment
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
BACKEND_TAR="lumin-backend-413720b1.tar.gz"
FRONTEND_TAR="lumin-frontend-6e3a3e20.tar.gz"
BACKEND_SHA="b8b5d890f2e0d6dfdf225740e561b9c6b2ef6854f0416c96e283936ef334bba5"
FRONTEND_SHA="f96bf8805f19b555fb6f3509e57c3bc241f3cbeaa0a54b175a90f1fc71ec5740"
EXPECTED_BUILD_ID="S0kC8_NAlhQyCLKMFHHdQ"
RELEASE_ID="${RELEASE_ID:-$(date +%Y%m%d_%H%M%S)}"
DRY_RUN=true
[[ "${1:-}" == "--execute" ]] && DRY_RUN=false

# Error trap for atomic rollback
ROLLBACK_NEEDED=false
ORIGINAL_BACKEND=""
ORIGINAL_FRONTEND=""

rollback_pair() {
  if $ROLLBACK_NEEDED; then
    echo ""
    echo "=== ROLLING BACK ==="
    if [ -n "$ORIGINAL_BACKEND" ] && [ -d "$ORIGINAL_BACKEND" ]; then
      sudo systemctl stop faztrack-attendance-lumin.service 2>/dev/null || true
      sudo systemctl stop faztrack-attendance-lumin-web.service 2>/dev/null || true
      rm -rf "$APP_DIR/backend"
      mv "$ORIGINAL_BACKEND" "$APP_DIR/backend"
      rm -rf "$APP_DIR/frontend"
      mv "$ORIGINAL_FRONTEND" "$APP_DIR/frontend"
      sudo systemctl start faztrack-attendance-lumin.service
      sudo systemctl start faztrack-attendance-lumin-web.service
      echo "ROLLBACK COMPLETE"
    fi
  fi
}
trap rollback_pair ERR

echo "=== LUMIN PRODUCTION DEPLOY V3 ==="
echo "Release ID: $RELEASE_ID"
echo "Dry-run: $DRY_RUN"
echo ""

# ── VALIDATE ENVIRONMENT ──
if [ "$(hostname)" != "$EXPECTED_HOSTNAME" ]; then
  echo "FATAL: Hostname mismatch. Expected $EXPECTED_HOSTNAME, got $(hostname)"
  exit 1
fi
if [ "$(whoami)" != "$EXPECTED_USER" ]; then
  echo "FATAL: Must run as $EXPECTED_USER"
  exit 1
fi

# ── HELPERS ──
run() {
  if $DRY_RUN; then echo "[DRY-RUN] $*"; else echo "[EXEC] $*"; eval "$@"; fi
}
fail() { echo "FATAL: $1"; exit 1; }

# ── 1. VERIFY ARTIFACTS ──
echo "--- Artifact Verification ---"
[ -f "$BACKEND_TAR" ] || fail "Backend artifact not found: $BACKEND_TAR"
[ -f "$FRONTEND_TAR" ] || fail "Frontend artifact not found: $FRONTEND_TAR"

# SHA256 verification
echo "Verifying SHA256..."
ACTUAL_BE=$(sha256sum "$BACKEND_TAR" | awk '{print $1}')
ACTUAL_FE=$(sha256sum "$FRONTEND_TAR" | awk '{print $1}')
[ "$ACTUAL_BE" = "$BACKEND_SHA" ] || fail "Backend SHA256 mismatch: expected $BACKEND_SHA, got $ACTUAL_BE"
[ "$ACTUAL_FE" = "$FRONTEND_SHA" ] || fail "Frontend SHA256 mismatch: expected $FRONTEND_SHA, got $ACTUAL_FE"
echo "  Backend SHA256: PASS"
echo "  Frontend SHA256: PASS"

# ── 2. VERIFY BACKUP ──
echo ""
echo "--- Backup Verification ---"
# Find latest backup
BACKUP_DIR=$(ls -d "$BACKUP_BASE"/*/ 2>/dev/null | sort -r | head -1)
if [ -z "$BACKUP_DIR" ]; then
  fail "No backup found in $BACKUP_BASE"
fi
echo "Backup: $BACKUP_DIR"

# Verify BACKUP-MANIFEST exists
[ -f "$BACKUP_DIR/BACKUP-MANIFEST.md" ] || fail "BACKUP-MANIFEST.md not found"

# Verify SHA256
cd "$BACKUP_DIR"
if ! sha256sum -c checksums-sha256.txt > /dev/null 2>&1; then
  fail "Backup SHA256 verification failed"
fi
echo "  SHA256: PASS"

# Verify pg_restore --list
DUMP_FILE=$(ls db-dump-*.dump 2>/dev/null | head -1)
if [ -n "$DUMP_FILE" ]; then
  if ! docker exec -i "$PG_CONTAINER" pg_restore --list < "$DUMP_FILE" > /dev/null 2>&1; then
    fail "pg_restore --list failed"
  fi
  echo "  pg_restore --list: PASS"
fi

# Verify same hostname
BACKUP_HOST=$(grep "Hostname:" "$BACKUP_DIR/BACKUP-MANIFEST.md" | awk '{print $2}')
if [ "$BACKUP_HOST" != "$(hostname)" ]; then
  fail "Backup hostname mismatch: $BACKUP_HOST vs $(hostname)"
fi
echo "  Hostname: PASS"

cd "$APP_DIR"

# ── 3. INVENTORY PERSISTENT DATA ──
echo ""
echo "--- Persistent Data Inventory ---"
PERSISTENT_DIRS=()
for dir in uploads runtime persistent; do
  if [ -d "$APP_DIR/$dir" ]; then
    PERSISTENT_DIRS+=("$dir")
    echo "  Found: $dir"
  fi
done

# ── 4. STAGE RELEASE ──
echo ""
echo "--- Staging ---"
STAGING_DIR="$APP_DIR/releases/$RELEASE_ID"
if [ -d "$STAGING_DIR" ]; then
  fail "Staging directory already exists: $STAGING_DIR"
fi
run "mkdir -p $APP_DIR/releases"
run "mkdir $STAGING_DIR"

# Extract backend
echo "  Extracting backend..."
run "mkdir -p $STAGING_DIR/backend"
run "tar xzf $BACKEND_TAR -C $STAGING_DIR/backend --strip-components=1"

# Copy venv from current (Option A)
echo "  Copying .venv..."
run "cp -a $APP_DIR/backend/.venv $STAGING_DIR/backend/.venv"

# Copy .env.lumin
echo "  Copying .env.lumin..."
run "cp $APP_DIR/backend/.env.lumin $STAGING_DIR/backend/.env.lumin"

# Extract frontend
echo "  Extracting frontend..."
run "mkdir -p $STAGING_DIR/frontend"
run "tar xzf $FRONTEND_TAR -C $STAGING_DIR/frontend"

# Copy .env.local
echo "  Copying .env.local..."
if [ -f "$APP_DIR/frontend/.env.local" ]; then
  run "cp $APP_DIR/frontend/.env.local $STAGING_DIR/frontend/.env.local"
fi

# Copy persistent data
for dir in "${PERSISTENT_DIRS[@]}"; do
  echo "  Copying $dir..."
  run "cp -a $APP_DIR/$dir $STAGING_DIR/$dir"
done

# ── 5. VALIDATE STAGED RELEASE ──
echo ""
echo "--- Staged Validation ---"
if ! $DRY_RUN; then
  # Backend
  [ -x "$STAGING_DIR/backend/.venv/bin/uvicorn" ] || fail "uvicorn not executable"
  [ -f "$STAGING_DIR/backend/.env.lumin" ] || fail ".env.lumin missing"
  [ -f "$STAGING_DIR/backend/app/main.py" ] || fail "main.py missing"
  echo "  Backend: PASS"
  
  # Frontend
  [ -f "$STAGING_DIR/frontend/.next/standalone/server.js" ] || fail "server.js missing"
  [ -d "$STAGING_DIR/frontend/.next/static" ] || fail ".next/static missing"
  [ -d "$STAGING_DIR/frontend/public" ] || fail "public/ missing"
  echo "  Frontend: PASS"
  
  # Ownership
  OWNER=$(stat -c '%U' "$STAGING_DIR/backend/.venv/bin/uvicorn")
  [ "$OWNER" = "ubuntu" ] || fail "Wrong owner: $OWNER"
  echo "  Ownership: PASS"
  
  # BUILD_ID
  STAGED_BUILD_ID=$(cat "$STAGING_DIR/frontend/.next/BUILD_ID" 2>/dev/null || echo "N/A")
  [ "$STAGED_BUILD_ID" = "$EXPECTED_BUILD_ID" ] || fail "BUILD_ID mismatch: $STAGED_BUILD_ID"
  echo "  BUILD_ID: PASS ($STAGED_BUILD_ID)"
fi

# ── 6. PRE-SWITCH HEALTH CHECK ──
echo ""
echo "--- Pre-switch Health ---"
if ! $DRY_RUN; then
  BE_OK=$(curl -sf http://localhost:8011/health/live 2>/dev/null && echo "OK" || echo "FAIL")
  FE_OK=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login 2>/dev/null || echo "FAIL")
  echo "  Backend: $BE_OK"
  echo "  Frontend: $FE_OK"
  [ "$BE_OK" = "OK" ] || fail "Backend health check failed"
  [ "$FE_OK" = "200" ] || fail "Frontend health check failed"
fi

# ── 7. ATOMIC SWITCH ──
echo ""
echo "--- Atomic Switch ---"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OLD_BACKEND="$APP_DIR/releases/old-backend-$TIMESTAMP"
OLD_FRONTEND="$APP_DIR/releases/old-frontend-$TIMESTAMP"

if ! $DRY_RUN; then
  ROLLBACK_NEEDED=true
  ORIGINAL_BACKEND="$OLD_BACKEND"
  ORIGINAL_FRONTEND="$OLD_FRONTEND"
fi

# Stop services
echo "  Stopping services..."
run "sudo systemctl stop faztrack-attendance-lumin.service"
run "sudo systemctl stop faztrack-attendance-lumin-web.service"

# Move current to old
echo "  Preserving current release..."
run "mv $APP_DIR/backend $OLD_BACKEND"
run "mv $APP_DIR/frontend $OLD_FRONTEND"

# Move staged to current
echo "  Activating new release..."
run "mv $STAGING_DIR/backend $APP_DIR/backend"
run "mv $STAGING_DIR/frontend $APP_DIR/frontend"

# Start services
echo "  Starting services..."
run "sudo systemctl start faztrack-attendance-lumin.service"
run "sudo systemctl start faztrack-attendance-lumin-web.service"
run "sleep 3"

ROLLBACK_NEEDED=false

# ── 8. POST-DEPLOY VALIDATION ──
echo ""
echo "--- Post-deploy Validation ---"
if ! $DRY_RUN; then
  # Systemd
  BE_STATUS=$(systemctl is-active faztrack-attendance-lumin.service 2>/dev/null || echo "inactive")
  FE_STATUS=$(systemctl is-active faztrack-attendance-lumin-web.service 2>/dev/null || echo "inactive")
  echo "  Backend service: $BE_STATUS"
  echo "  Frontend service: $FE_STATUS"
  [ "$BE_STATUS" = "active" ] || fail "Backend service not active"
  [ "$FE_STATUS" = "active" ] || fail "Frontend service not active"
  
  # Health
  BE_HEALTH=$(curl -sf http://localhost:8011/health/live 2>/dev/null || echo "FAIL")
  FE_HEALTH=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login 2>/dev/null || echo "FAIL")
  PUB_HEALTH=$(curl -sf -o /dev/null -w '%{http_code}' https://attendance-lumin.gofaztrack.com/login 2>/dev/null || echo "FAIL")
  ABSEN_HEALTH=$(curl -sf -o /dev/null -w '%{http_code}' https://attendance-lumin.gofaztrack.com/absen 2>/dev/null || echo "FAIL")
  echo "  Backend /health/live: $BE_HEALTH"
  echo "  Frontend /login: $FE_HEALTH"
  echo "  Public /login: $PUB_HEALTH"
  echo "  Public /absen: $ABSEN_HEALTH"
  
  # BUILD_ID
  DEPLOYED_BUILD=$(cat "$APP_DIR/frontend/.next/BUILD_ID" 2>/dev/null || echo "N/A")
  echo "  BUILD_ID: $DEPLOYED_BUILD"
  
  # Chunk hashes
  ADMIN_CHUNK=$(ls "$APP_DIR/frontend/.next/static/chunks/app/admin/page-"*.js 2>/dev/null | xargs -I{} basename {} | head -1)
  DASH_CHUNK=$(ls "$APP_DIR/frontend/.next/static/chunks/app/dashboard/page-"*.js 2>/dev/null | xargs -I{} basename {} | head -1)
  ABSEN_CHUNK=$(ls "$APP_DIR/frontend/.next/static/chunks/app/absen/page-"*.js 2>/dev/null | xargs -I{} basename {} | head -1)
  echo "  Admin chunk: $ADMIN_CHUNK"
  echo "  Dashboard chunk: $DASH_CHUNK"
  echo "  Absen chunk: $ABSEN_CHUNK"
  
  # Auto-rollback on failure
  if [ "$BE_HEALTH" = "FAIL" ] || [ "$FE_HEALTH" = "FAIL" ] || [ "$PUB_HEALTH" != "200" ]; then
    echo ""
    echo "=== POST-DEPLOY HEALTH FAILED — ROLLING BACK ==="
    ROLLBACK_NEEDED=true
    rollback_pair
    exit 1
  fi
fi

echo ""
echo "=== DEPLOY COMPLETE ==="
echo "Release: $RELEASE_ID"
echo "Old release preserved at: $OLD_BACKEND, $OLD_FRONTEND"
echo "Estimated downtime: ~10 seconds"
echo ""
echo "To rollback: bash lumin-prod-rollback.sh --execute"
