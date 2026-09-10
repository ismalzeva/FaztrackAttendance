#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-rollback-v3.sh — Production rollback (paired)
# Default: DRY-RUN. Use --execute to actually run.
# ─────────────────────────────────────────────────────────
set -euo pipefail

EXPECTED_HOSTNAME="VM-8-230-ubuntu"
EXPECTED_USER="ubuntu"
APP_DIR="/home/ubuntu/apps/attendance-lumin"
DRY_RUN=true
[[ "${1:-}" == "--execute" ]] && DRY_RUN=false

echo "=== LUMIN PRODUCTION ROLLBACK V3 ==="
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

run() {
  if $DRY_RUN; then echo "[DRY-RUN] $*"; else echo "[EXEC] $*"; eval "$@"; fi
}
fail() { echo "FATAL: $1"; exit 1; }

# ── FIND PREVIOUS RELEASE ──
echo "--- Finding Previous Release ---"
OLD_BACKEND=$(ls -d "$APP_DIR/releases/old-backend-"* 2>/dev/null | sort -r | head -1)
OLD_FRONTEND=$(ls -d "$APP_DIR/releases/old-frontend-"* 2>/dev/null | sort -r | head -1)

if [ -z "$OLD_BACKEND" ] || [ -z "$OLD_FRONTEND" ]; then
  fail "No previous release found. Cannot rollback."
fi

# Verify paired release (same timestamp)
BE_TS=$(basename "$OLD_BACKEND" | sed 's/old-backend-//')
FE_TS=$(basename "$OLD_FRONTEND" | sed 's/old-frontend-//')
if [ "$BE_TS" != "$FE_TS" ]; then
  fail "Backend and frontend old releases have different timestamps: $BE_TS vs $FE_TS. Paired rollback required."
fi
echo "Previous release: $BE_TS"
echo "Backend: $OLD_BACKEND"
echo "Frontend: $OLD_FRONTEND"

# Validate old release exists
[ -d "$OLD_BACKEND/app" ] || fail "Old backend app/ missing"
[ -d "$OLD_FRONTEND/.next" ] || fail "Old frontend .next/ missing"

# ── ROLLBACK ──
echo ""
echo "--- Rollback ---"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FAILED_RELEASE="$APP_DIR/releases/failed-$TIMESTAMP"

# Stop services
echo "  Stopping services..."
run "sudo systemctl stop faztrack-attendance-lumin.service"
run "sudo systemctl stop faztrack-attendance-lumin-web.service"

# Move current to failed
echo "  Preserving failed release..."
run "mv $APP_DIR/backend $FAILED_RELEASE/backend"
run "mv $APP_DIR/frontend $FAILED_RELEASE/frontend"

# Restore old release
echo "  Restoring previous release..."
run "mv $OLD_BACKEND $APP_DIR/backend"
run "mv $OLD_FRONTEND $APP_DIR/frontend"

# Start services
echo "  Starting services..."
run "sudo systemctl start faztrack-attendance-lumin.service"
run "sudo systemctl start faztrack-attendance-lumin-web.service"
run "sleep 3"

# ── HEALTH CHECK ──
echo ""
echo "--- Health Check ---"
if ! $DRY_RUN; then
  BE_OK=$(curl -sf http://localhost:8011/health/live 2>/dev/null && echo "OK" || echo "FAIL")
  FE_OK=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login 2>/dev/null || echo "FAIL")
  PUB_OK=$(curl -sf -o /dev/null -w '%{http_code}' https://attendance-lumin.gofaztrack.com/login 2>/dev/null || echo "FAIL")
  echo "  Backend: $BE_OK"
  echo "  Frontend: $FE_OK"
  echo "  Public: $PUB_OK"
  
  if [ "$BE_OK" != "OK" ] || [ "$FE_OK" != "200" ]; then
    echo ""
    echo "WARNING: Rollback health check failed. Manual intervention required."
    echo "Failed release at: $FAILED_RELEASE"
    exit 1
  fi
fi

echo ""
echo "=== ROLLBACK COMPLETE ==="
echo "Failed release preserved at: $FAILED_RELEASE"
echo ""
echo "NOTE: Database was NOT rolled back."
echo "Database restoration requires separate Owner/DBA approval."
