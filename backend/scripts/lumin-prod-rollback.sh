#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-rollback.sh — Production rollback v4
# Requires: --release-pair <path> (exact release-pair directory)
# Default: DRY-RUN. Use --execute to actually run.
# ─────────────────────────────────────────────────────────
set -euo pipefail

EXPECTED_HOSTNAME="VM-8-230-ubuntu"
EXPECTED_USER="ubuntu"
APP_DIR="/home/ubuntu/apps/attendance-lumin"
DRY_RUN=true
RELEASE_PAIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) DRY_RUN=false; shift ;;
    --release-pair) RELEASE_PAIR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

fatal() { echo "FATAL: $1"; exit 1; }
run() { if $DRY_RUN; then echo "[DRY-RUN] $*"; else "$@"; fi }

echo "=== LUMIN PRODUCTION ROLLBACK V4 ==="
echo "Dry-run: $DRY_RUN"
echo ""

# ── VALIDATE ENVIRONMENT ──
[ "$(hostname)" = "$EXPECTED_HOSTNAME" ] || fatal "Hostname mismatch"
[ "$(whoami)" = "$EXPECTED_USER" ] || fatal "Must run as $EXPECTED_USER"

# ── VALIDATE RELEASE PAIR ──
[ -n "$RELEASE_PAIR" ] || fatal "Missing --release-pair argument"
[ -d "$RELEASE_PAIR" ] || fatal "Release pair not found: $RELEASE_PAIR"
[ -d "$RELEASE_PAIR/backend-old" ] || fatal "Old backend not found"
[ -d "$RELEASE_PAIR/frontend-old" ] || fatal "Old frontend not found"

# Validate old release
[ -d "$RELEASE_PAIR/backend-old/app" ] || fatal "Old backend app/ missing"
[ -d "$RELEASE_PAIR/backend-old/.venv" ] || fatal "Old backend .venv missing"
[ -f "$RELEASE_PAIR/backend-old/.env.lumin" ] || fatal "Old backend .env.lumin missing"
[ -f "$RELEASE_PAIR/frontend-old/.next/standalone/server.js" ] || fatal "Old frontend server.js missing"
[ -d "$RELEASE_PAIR/frontend-old/.next/static" ] || fatal "Old frontend .next/static missing"
echo "Old release validated."

# ── ROLLBACK ──
echo ""
echo "--- Rollback ---"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FAILED_RELEASE="$APP_DIR/releases/failed-rollback-$TIMESTAMP"
mkdir -p "$FAILED_RELEASE"

echo "  Stopping services..."
run sudo systemctl stop faztrack-attendance-lumin.service
run sudo systemctl stop faztrack-attendance-lumin-web.service

echo "  Moving current to failed-release..."
run mv "$APP_DIR/backend" "$FAILED_RELEASE/backend"
run mv "$APP_DIR/frontend" "$FAILED_RELEASE/frontend"

echo "  Restoring old release..."
run mv "$RELEASE_PAIR/backend-old" "$APP_DIR/backend"
run mv "$RELEASE_PAIR/frontend-old" "$APP_DIR/frontend"

echo "  Starting services..."
run sudo systemctl start faztrack-attendance-lumin.service
run sudo systemctl start faztrack-attendance-lumin-web.service
run sleep 3

# ── HEALTH CHECKS ──
echo ""
echo "--- Health Checks ---"
if ! $DRY_RUN; then
  curl -sf http://localhost:8011/health/live > /dev/null 2>&1 || fatal "Backend health failed"
  curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login | grep -q "200" || fatal "Frontend health failed"
  curl -sf -o /dev/null -w '%{http_code}' https://attendance-lumin.gofaztrack.com/login | grep -q "200" || fatal "Public /login failed"
  curl -sf -o /dev/null -w '%{http_code}' https://attendance-lumin.gofaztrack.com/absen | grep -q "200" || fatal "Public /absen failed"
  echo "  All health checks: PASS"
fi

echo ""
echo "=== ROLLBACK COMPLETE ==="
echo "Failed release: $FAILED_RELEASE"
echo ""
echo "NOTE: Database was NOT rolled back."
echo "Database restoration requires separate Owner/DBA approval."
