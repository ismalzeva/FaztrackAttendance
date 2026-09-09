#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-rollback.sh — Rollback to previous release
# Default: DRY-RUN. Use --execute to actually run.
# ─────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="/home/ubuntu/apps/attendance-lumin"
DRY_RUN=true
[[ "${1:-}" == "--execute" ]] && DRY_RUN=false

echo "=== LUMIN PRODUCTION ROLLBACK ==="
echo "Dry-run: $DRY_RUN"
echo ""

run() {
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    echo "[EXEC] $*"
    eval "$@"
  fi
}

# Check old release exists
if [ ! -d "$APP_DIR/backend-old" ] && [ ! -d "$APP_DIR/frontend-old" ]; then
  echo "ERROR: No old release found at $APP_DIR/backend-old or frontend-old"
  echo "Nothing to rollback to."
  exit 1
fi

# Rollback backend
if [ -d "$APP_DIR/backend-old" ]; then
  echo "--- Backend rollback ---"
  run "mv $APP_DIR/backend $APP_DIR/backend-failed"
  run "mv $APP_DIR/backend-old $APP_DIR/backend"
  run "sudo systemctl restart faztrack-attendance-lumin.service"
  run "sleep 3"
else
  echo "--- Backend: no old release found, skipping ---"
fi

# Rollback frontend
if [ -d "$APP_DIR/frontend-old" ]; then
  echo "--- Frontend rollback ---"
  run "mv $APP_DIR/frontend $APP_DIR/frontend-failed"
  run "mv $APP_DIR/frontend-old $APP_DIR/frontend"
  run "sudo systemctl restart faztrack-attendance-lumin-web.service"
  run "sleep 3"
else
  echo "--- Frontend: no old release found, skipping ---"
fi

# Health check
echo ""
echo "--- Health check ---"
if ! $DRY_RUN; then
  BE=$(curl -sf http://localhost:8011/health/live 2>/dev/null || echo "FAIL")
  FE=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login 2>/dev/null || echo "FAIL")
  echo "Backend: $BE"
  echo "Frontend: $FE"
  
  if [ "$BE" != "FAIL" ] && [ "$FE" != "FAIL" ]; then
    echo ""
    echo "=== ROLLBACK SUCCESSFUL ==="
    echo "Failed release preserved at: $APP_DIR/backend-failed, frontend-failed"
  else
    echo ""
    echo "=== ROLLBACK HEALTH CHECK FAILED ==="
    echo "Manual intervention required."
    exit 1
  fi
else
  echo "[DRY-RUN] Would verify health endpoints"
fi

# Note about database
echo ""
echo "NOTE: Database was NOT rolled back."
echo "If database changes were made, restore manually from backup:"
echo "  docker exec attendance-lumin-postgres pg_restore -U faztrack_lumin -d faztrack_attendance_lumin <backup.dump>"
