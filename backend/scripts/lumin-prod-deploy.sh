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

fail() {
  echo "FATAL: $1"
  echo "DEPLOYMENT ABORTED."
  exit 1
}

# Validate hostname
if [ "$(hostname)" != "VM-3-20-ubuntu" ] && [ "$(hostname)" != "lumin-production" ]; then
  echo "WARNING: Unexpected hostname: $(hostname)"
fi

# Check release files exist
BACKEND_TAR=$(ls -t lumin-backend-*.tar.gz 2>/dev/null | head -1)
FRONTEND_TAR=$(ls -t lumin-frontend-*.tar.gz 2>/dev/null | head -1)
[ -z "$BACKEND_TAR" ] && fail "Backend tarball not found"
[ -z "$FRONTEND_TAR" ] && fail "Frontend tarball not found"
echo "Backend: $BACKEND_TAR"
echo "Frontend: $FRONTEND_TAR"

# Validate SHA256
echo ""
echo "--- Validate SHA256 ---"
if [ -f "checksums-sha256.txt" ]; then
  sha256sum -c checksums-sha256.txt || fail "SHA256 validation failed"
  echo "SHA256: OK"
else
  echo "WARNING: No checksums file found. Skipping validation."
fi

# Pre-deploy validation
echo ""
echo "--- Pre-deploy Validation ---"

# Check .venv exists
if [ ! -d "$APP_DIR/backend/.venv" ] && [ ! -L "$APP_DIR/backend/.venv" ]; then
  fail "backend/.venv not found"
fi
echo "backend/.venv: OK"

# Check .venv/bin/uvicorn executable
if [ ! -x "$APP_DIR/backend/.venv/bin/uvicorn" ]; then
  fail "backend/.venv/bin/uvicorn not executable"
fi
echo "backend/.venv/bin/uvicorn: OK"

# Check .env.lumin exists
if [ ! -f "$APP_DIR/backend/.env.lumin" ]; then
  fail "backend/.env.lumin not found"
fi
echo "backend/.env.lumin: OK"

# Check frontend .env.local
if [ ! -f "$APP_DIR/frontend/.env.local" ]; then
  echo "WARNING: frontend/.env.local not found"
else
  echo "frontend/.env.local: OK"
fi

# Check frontend server.js
if [ ! -f "$APP_DIR/frontend/.next/standalone/server.js" ]; then
  fail "frontend/.next/standalone/server.js not found"
fi
echo "frontend/.next/standalone/server.js: OK"

# Check frontend .next/static
if [ ! -d "$APP_DIR/frontend/.next/static" ]; then
  fail "frontend/.next/static not found"
fi
echo "frontend/.next/static: OK"

# Check frontend public
if [ -d "$APP_DIR/frontend/public" ]; then
  echo "frontend/public: OK ($(ls "$APP_DIR/frontend/public" 2>/dev/null | wc -l) files)"
else
  echo "WARNING: frontend/public not found"
fi

# Record pre-deploy state
echo ""
echo "--- Pre-deploy State ---"
PRE_BE_MAIN_SHA=$(sha256sum "$APP_DIR/backend/app/main.py" 2>/dev/null | cut -c1-16 || echo "N/A")
PRE_FE_BUILD=$(cat "$APP_DIR/frontend/.next/BUILD_ID" 2>/dev/null || echo "N/A")
echo "backend/app/main.py hash: $PRE_BE_MAIN_SHA"
echo "frontend BUILD_ID: $PRE_FE_BUILD"

# Health check before
echo ""
echo "--- Health Check (pre-deploy) ---"
BE_HEALTH=$(curl -sf http://localhost:8011/health/live 2>/dev/null || echo "FAIL")
FE_HEALTH=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login 2>/dev/null || echo "FAIL")
echo "Backend: $BE_HEALTH"
echo "Frontend: $FE_HEALTH"

# Deploy backend
echo ""
echo "--- Deploy Backend ---"
run "mkdir -p $APP_DIR/backend-new"
run "tar xzf $BACKEND_TAR -C $APP_DIR/backend-new --strip-components=1"

# Copy .venv (preserve)
echo "  Preserving .venv..."
run "ln -sf $APP_DIR/backend/.venv $APP_DIR/backend-new/.venv"

# Copy .env.lumin (preserve)
echo "  Preserving .env.lumin..."
run "cp $APP_DIR/backend/.env.lumin $APP_DIR/backend-new/.env.lumin"

# Validate new backend
if ! $DRY_RUN; then
  [ -x "$APP_DIR/backend-new/.venv/bin/uvicorn" ] || fail "New backend .venv/bin/uvicorn not executable"
  [ -f "$APP_DIR/backend-new/.env.lumin" ] || fail "New backend .env.lumin not found"
  echo "  New backend validated."
fi

# Atomic switch backend
run "mv $APP_DIR/backend $APP_DIR/backend-old"
run "mv $APP_DIR/backend-new $APP_DIR/backend"

# Deploy frontend
echo ""
echo "--- Deploy Frontend ---"
run "mkdir -p $APP_DIR/frontend-new"
run "tar xzf $FRONTEND_TAR -C $APP_DIR/frontend-new"

# Copy .env.local (preserve)
echo "  Preserving .env.local..."
run "cp $APP_DIR/frontend/.env.local $APP_DIR/frontend-new/.env.local 2>/dev/null || true"

# Validate new frontend
if ! $DRY_RUN; then
  [ -f "$APP_DIR/frontend-new/.next/standalone/server.js" ] || fail "New frontend server.js not found"
  [ -d "$APP_DIR/frontend-new/.next/static" ] || fail "New frontend .next/static not found"
  echo "  New frontend validated."
fi

# Atomic switch frontend
run "mv $APP_DIR/frontend $APP_DIR/frontend-old"
run "mv $APP_DIR/frontend-new $APP_DIR/frontend"

# Restart services
echo ""
echo "--- Restart Services ---"
run "sudo systemctl restart faztrack-attendance-lumin.service"
run "sleep 3"
run "sudo systemctl restart faztrack-attendance-lumin-web.service"
run "sleep 3"

# Health check after
echo ""
echo "--- Health Check (post-deploy) ---"
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

echo ""
echo "--- Post-deploy ---"
echo "Old release preserved at: $APP_DIR/backend-old, $APP_DIR/frontend-old"
echo "To rollback: bash lumin-prod-rollback.sh --execute"

echo ""
echo "=== DEPLOY COMPLETE ==="
echo "Estimated downtime: ~10 seconds (service restart)"
echo "No services were permanently stopped."
