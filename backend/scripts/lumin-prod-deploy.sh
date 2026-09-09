#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-deploy.sh — Deploy release to production
# Default: DRY-RUN. Use --execute to actually run.
# Requires: release tar.gz files in current directory
# ─────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="/home/ubuntu/apps/attendance-lumin"
PG_CONTAINER="attendance-lumin-postgres"
DRY_RUN=true
[[ "${1:-}" == "--execute" ]] && DRY_RUN=false

echo "=== LUMIN PRODUCTION DEPLOY ==="
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

# Validate hostname
if [ "$(hostname)" != "VM-3-20-ubuntu" ] && [ "$(hostname)" != "lumin-production" ]; then
  echo "WARNING: Unexpected hostname: $(hostname)"
fi

# Check release files exist
BACKEND_TAR=$(ls -t lumin-backend-*.tar.gz 2>/dev/null | head -1)
FRONTEND_TAR=$(ls -t lumin-frontend-*.tar.gz 2>/dev/null | head -1)
if [ -z "$BACKEND_TAR" ] || [ -z "$FRONTEND_TAR" ]; then
  echo "ERROR: Release tarballs not found in current directory"
  echo "Expected: lumin-backend-*.tar.gz, lumin-frontend-*.tar.gz"
  exit 1
fi
echo "Backend: $BACKEND_TAR"
echo "Frontend: $FRONTEND_TAR"

# Record pre-deploy state
echo ""
echo "--- Pre-deploy state ---"
PRE_BE_SHA=$(cat "$APP_DIR/backend/app/main.py" 2>/dev/null | sha256sum | cut -c1-16 || echo "N/A")
PRE_FE_BUILD=$(cat "$APP_DIR/frontend/.next/BUILD_ID" 2>/dev/null || echo "N/A")
echo "Backend main.py hash: $PRE_BE_SHA"
echo "Frontend BUILD_ID: $PRE_FE_BUILD"

# Health check before
echo ""
echo "--- Health check (pre-deploy) ---"
BE_HEALTH=$(curl -sf http://localhost:8011/health/live 2>/dev/null || echo "FAIL")
FE_HEALTH=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login 2>/dev/null || echo "FAIL")
echo "Backend: $BE_HEALTH"
echo "Frontend: $FE_HEALTH"

# Deploy backend
echo ""
echo "--- Deploy backend ---"
run "mkdir -p $APP_DIR/backend-new"
run "tar xzf $BACKEND_TAR -C $APP_DIR/backend-new --strip-components=1"
# Copy .env from current
run "cp $APP_DIR/backend/.env $APP_DIR/backend-new/.env 2>/dev/null || true"
# Atomic switch
run "mv $APP_DIR/backend $APP_DIR/backend-old"
run "mv $APP_DIR/backend-new $APP_DIR/backend"

# Deploy frontend
echo ""
echo "--- Deploy frontend ---"
run "mkdir -p $APP_DIR/frontend-new"
run "tar xzf $FRONTEND_TAR -C $APP_DIR/frontend-new"
# Copy .env.local from current
run "cp $APP_DIR/frontend/.env.local $APP_DIR/frontend-new/.env.local 2>/dev/null || true"
# Atomic switch
run "mv $APP_DIR/frontend $APP_DIR/frontend-old"
run "mv $APP_DIR/frontend-new $APP_DIR/frontend"

# Restart services
echo ""
echo "--- Restart services ---"
run "sudo systemctl restart faztrack-attendance-lumin.service"
run "sleep 3"
run "sudo systemctl restart faztrack-attendance-lumin-web.service"
run "sleep 3"

# Health check after
echo ""
echo "--- Health check (post-deploy) ---"
if ! $DRY_RUN; then
  BE_HEALTH2=$(curl -sf http://localhost:8011/health/live 2>/dev/null || echo "FAIL")
  FE_HEALTH2=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login 2>/dev/null || echo "FAIL")
  echo "Backend: $BE_HEALTH2"
  echo "Frontend: $FE_HEALTH2"
  
  if [ "$BE_HEALTH2" = "FAIL" ] || [ "$FE_HEALTH2" = "FAIL" ]; then
    echo ""
    echo "=== HEALTH CHECK FAILED — ROLLING BACK ==="
    mv $APP_DIR/backend $APP_DIR/backend-failed
    mv $APP_DIR/backend-old $APP_DIR/backend
    mv $APP_DIR/frontend $APP_DIR/frontend-failed
    mv $APP_DIR/frontend-old $APP_DIR/frontend
    sudo systemctl restart faztrack-attendance-lumin.service
    sudo systemctl restart faztrack-attendance-lumin-web.service
    echo "ROLLBACK COMPLETE"
    exit 1
  fi
fi

# Cleanup old (keep for rollback)
echo ""
echo "--- Post-deploy ---"
echo "Old release preserved at: $APP_DIR/backend-old, $APP_DIR/frontend-old"
echo "To rollback: bash lumin-prod-rollback.sh --execute"

echo ""
echo "=== DEPLOY COMPLETE ==="
echo "Estimated downtime: ~10 seconds (service restart)"
