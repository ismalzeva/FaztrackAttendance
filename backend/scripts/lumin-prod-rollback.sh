#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-rollback.sh — Production rollback v4.1
# Requires: --release-pair <exact path inside releases/>
# Default: DRY-RUN (zero mutation). Use --execute to actually run.
# ─────────────────────────────────────────────────────────
set -euo pipefail

EXPECTED_HOSTNAME="VM-8-230-ubuntu"
EXPECTED_USER="ubuntu"
APP_DIR="/home/ubuntu/apps/attendance-lumin"
RELEASES_DIR="$APP_DIR/releases"

# ── FIXTURE MODE (tests only; loud + explicit) ──
FIXTURE_MODE=false
if [ -n "${LUMIN_FIXTURE_ROOT:-}" ]; then
  FIXTURE_MODE=true
  APP_DIR="$LUMIN_FIXTURE_ROOT/app"
  RELEASES_DIR="$APP_DIR/releases"
  EXPECTED_HOSTNAME="$(hostname)"   # fixture: accept current host
  echo "############################################################"
  echo "# FIXTURE MODE ACTIVE — NOT PRODUCTION                        #"
  echo "# LUMIN_FIXTURE_ROOT=$LUMIN_FIXTURE_ROOT"
  echo "# APP_DIR=$APP_DIR"
  echo "############################################################"
fi
DRY_RUN=true
RELEASE_PAIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) DRY_RUN=false; shift ;;
    --release-pair) RELEASE_PAIR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── STATE ──
STATE="PRE_SWITCH"
FAILED_RELEASE=""
STAGES_MOVED=()   # track "current" components moved out
RECOVERY_ARMED=false

fatal_preflight() { echo ""; echo "FATAL (preflight — no mutation occurred): $1"; exit 1; }

recover() {
  echo ""
  echo "=== RECOVERY (state=$STATE) ==="
  case "$STATE" in
    PRE_SWITCH)
      echo "No mutation occurred. Nothing to recover."
      ;;
    SERVICES_STOPPED)
      echo "Services stopped, no moves. Restarting services..."
      $DRY_RUN || { sudo systemctl start faztrack-attendance-lumin.service 2>/dev/null || true
                    sudo systemctl start faztrack-attendance-lumin-web.service 2>/dev/null || true; }
      ;;
    CURRENT_PRESERVED|OLD_ACTIVATED)
      echo "Partial rollback detected. Restoring moved component(s)..."
      # Put back anything moved out of the app dir
      for c in "${STAGES_MOVED[@]}"; do
        if [ -d "$FAILED_RELEASE/$c" ] && [ ! -d "$APP_DIR/$c" ]; then
          mv "$FAILED_RELEASE/$c" "$APP_DIR/$c" 2>/dev/null || true
          echo "  Restored $c"
        fi
      done
      # Ensure both sides come from the recorded release-pair
      if [ -n "$RELEASE_PAIR" ]; then
        for c in backend frontend; do
          if [ ! -d "$APP_DIR/$c" ] && [ -d "$RELEASE_PAIR/$c-old" ]; then
            mv "$RELEASE_PAIR/$c-old" "$APP_DIR/$c" 2>/dev/null || true
            echo "  Restored $c from release-pair"
          fi
        done
      fi
      $DRY_RUN || { sudo systemctl start faztrack-attendance-lumin.service 2>/dev/null || true
                    sudo systemctl start faztrack-attendance-lumin-web.service 2>/dev/null || true; }
      if ! $DRY_RUN; then
        echo "  pair: backend=$([ -d "$APP_DIR/backend" ] && echo present || echo MISSING) frontend=$([ -d "$APP_DIR/frontend" ] && echo present || echo MISSING)"
        echo "  systemd backend : $(systemctl is-active faztrack-attendance-lumin.service 2>/dev/null || echo unknown)"
        echo "  systemd frontend: $(systemctl is-active faztrack-attendance-lumin-web.service 2>/dev/null || echo unknown)"
      fi
      ;;
    VERIFYING)
      echo "Rollback applied but verification failed. Preserving all directories."
      ;;
  esac
  echo ""
  echo "Recovery paths (nothing deleted):"
  echo "  Release pair   : ${RELEASE_PAIR:-<none>}"
  echo "  Failed release : ${FAILED_RELEASE:-<none>}"
  echo "  App backend    : $APP_DIR/backend"
  echo "  App frontend   : $APP_DIR/frontend"
  echo "=== RECOVERY END ==="
}

fatal_after_switch() {
  echo ""
  echo "FATAL (post-mutation): $1"
  recover
  exit 1
}

on_err() {
  if $RECOVERY_ARMED; then
    echo ""
    echo "ERROR trap fired (state=$STATE)"
    recover
  fi
  exit 1
}
trap on_err ERR

mx() {
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    echo "[EXEC] $*"
    if ! "$@"; then fatal_after_switch "Mutation failed: $*"; fi
  fi
}

echo "=== LUMIN PRODUCTION ROLLBACK V4.1 ==="
echo "Dry-run: $DRY_RUN"
echo ""

# ══════════════════════════════════════════
# PREFLIGHT (no mutation)
# ══════════════════════════════════════════
[ "$(hostname)" = "$EXPECTED_HOSTNAME" ] || fatal_preflight "Hostname mismatch: $(hostname)"
[ "$(whoami)" = "$EXPECTED_USER" ] || $FIXTURE_MODE || fatal_preflight "Must run as $EXPECTED_USER, got $(whoami)"
[ -n "$RELEASE_PAIR" ] || fatal_preflight "Missing --release-pair argument"
[ -d "$RELEASE_PAIR" ] || fatal_preflight "Release pair not found: $RELEASE_PAIR"

# Canonical path must be inside RELEASES_DIR
RELEASES_CANON=$(readlink -f "$RELEASES_DIR" 2>/dev/null || echo "$RELEASES_DIR")
PAIR_CANON=$(readlink -f "$RELEASE_PAIR" 2>/dev/null || echo "$RELEASE_PAIR")
case "$PAIR_CANON/" in
  "$RELEASES_CANON"/*) : ;;
  *) fatal_preflight "Release pair path outside releases dir: $PAIR_CANON" ;;
esac
echo "Release pair (canonical): $PAIR_CANON"

# Validate both old components exist
[ -d "$RELEASE_PAIR/backend-old" ] || fatal_preflight "Old backend missing: $RELEASE_PAIR/backend-old"
[ -d "$RELEASE_PAIR/frontend-old" ] || fatal_preflight "Old frontend missing: $RELEASE_PAIR/frontend-old"

# Validate old release contents
[ -d "$RELEASE_PAIR/backend-old/app" ] || fatal_preflight "Old backend app/ missing"
[ -d "$RELEASE_PAIR/backend-old/.venv" ] || fatal_preflight "Old backend .venv missing"
[ -f "$RELEASE_PAIR/backend-old/.env.lumin" ] || fatal_preflight "Old backend .env.lumin missing"
[ -f "$RELEASE_PAIR/frontend-old/.next/standalone/server.js" ] || fatal_preflight "Old frontend server.js missing"
[ -d "$RELEASE_PAIR/frontend-old/.next/static" ] || fatal_preflight "Old frontend .next/static missing"
echo "Old release validated."

# Failed-release target must not exist
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FAILED_RELEASE="$RELEASES_DIR/failed-rollback-$TIMESTAMP"
[ -d "$FAILED_RELEASE" ] && fatal_preflight "Failed-release target already exists: $FAILED_RELEASE"
echo "Failed-release target free: $FAILED_RELEASE"

# ── DRY-RUN STOPS HERE (no mutation) ──
if $DRY_RUN; then
  echo ""
  echo "[DRY-RUN] Would:"
  echo "  mkdir $FAILED_RELEASE"
  echo "  systemctl stop faztrack-attendance-lumin.service"
  echo "  systemctl stop faztrack-attendance-lumin-web.service"
  echo "  mv $APP_DIR/backend  -> $FAILED_RELEASE/backend"
  echo "  mv $APP_DIR/frontend -> $FAILED_RELEASE/frontend"
  echo "  mv $RELEASE_PAIR/backend-old  -> $APP_DIR/backend"
  echo "  mv $RELEASE_PAIR/frontend-old -> $APP_DIR/frontend"
  echo "  systemctl start (both)"
  echo "  health checks (local + public)"
  echo ""
  echo "=== DRY-RUN COMPLETE (zero filesystem changes) ==="
  exit 0
fi

# ══════════════════════════════════════════
# ROLLBACK (mutation begins)
# ══════════════════════════════════════════
echo ""
echo "--- Rollback ---"
RECOVERY_ARMED=true

STATE="PREPARING"
mx mkdir "$FAILED_RELEASE"

STATE="SERVICES_STOPPED"
mx sudo systemctl stop faztrack-attendance-lumin.service
mx sudo systemctl stop faztrack-attendance-lumin-web.service

STATE="CURRENT_PRESERVED"
STAGES_MOVED=()
if [ -d "$APP_DIR/backend" ]; then
  mx mv "$APP_DIR/backend" "$FAILED_RELEASE/backend"
  STAGES_MOVED+=("backend")
fi
if [ -d "$APP_DIR/frontend" ]; then
  mx mv "$APP_DIR/frontend" "$FAILED_RELEASE/frontend"
  STAGES_MOVED+=("frontend")
fi

STATE="OLD_ACTIVATED"
mx mv "$RELEASE_PAIR/backend-old" "$APP_DIR/backend"
mx mv "$RELEASE_PAIR/frontend-old" "$APP_DIR/frontend"

STATE="VERIFYING"
mx sudo systemctl start faztrack-attendance-lumin.service
mx sudo systemctl start faztrack-attendance-lumin-web.service
sleep 3

# ── PAIR + SERVICE + HEALTH CHECKS ──
echo "--- Pair Integrity ---"
[ -d "$APP_DIR/backend" ] || fatal_after_switch "Pair incomplete: backend missing after rollback"
[ -d "$APP_DIR/frontend" ] || fatal_after_switch "Pair incomplete: frontend missing after rollback"
[ -f "$APP_DIR/backend/app/main.py" ] || fatal_after_switch "Restored backend is not a valid release"
[ -f "$APP_DIR/frontend/.next/standalone/server.js" ] || fatal_after_switch "Restored frontend is not a valid release"
echo "  backend + frontend both restored and valid"

echo "--- Service State ---"
BS="$(systemctl is-active faztrack-attendance-lumin.service 2>/dev/null || echo unknown)"
FS="$(systemctl is-active faztrack-attendance-lumin-web.service 2>/dev/null || echo unknown)"
echo "  systemd backend : $BS"
echo "  systemd frontend: $FS"
[ "$BS" = "active" ] || fatal_after_switch "Backend service not active after rollback ($BS)"
[ "$FS" = "active" ] || fatal_after_switch "Frontend service not active after rollback ($FS)"

echo "--- Health Checks ---"
curl -sf http://localhost:8011/health/live > /dev/null 2>&1 || fatal_after_switch "Backend health failed after rollback"
[ "$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login)" = "200" ] || fatal_after_switch "Local frontend /login failed after rollback"
[ "$(curl -sf -o /dev/null -w '%{http_code}' https://attendance-lumin.gofaztrack.com/login)" = "200" ] || fatal_after_switch "Public /login failed after rollback"
[ "$(curl -sf -o /dev/null -w '%{http_code}' https://attendance-lumin.gofaztrack.com/absen)" = "200" ] || fatal_after_switch "Public /absen failed after rollback"
echo "  local + public health: PASS"

STATE="VERIFIED"
RECOVERY_ARMED=false

echo ""
echo "=== ROLLBACK COMPLETE (VERIFIED) ==="
echo "Failed release preserved: $FAILED_RELEASE"
echo ""
echo "NOTE: Database was NOT rolled back."
echo "Database restoration requires separate Owner/DBA approval."
