#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-deploy.sh — Production deploy v4.1
# Default: DRY-RUN. Use --execute to actually run.
# Requires: --backup-dir <exact path>
# ─────────────────────────────────────────────────────────
set -euo pipefail

EXPECTED_HOSTNAME="VM-8-230-ubuntu"
EXPECTED_USER="ubuntu"
APP_DIR="/home/ubuntu/apps/attendance-lumin"
RELEASES_DIR="$APP_DIR/releases"

PG_CONTAINER="attendance-lumin-postgres"
EXPECTED_BUILD_ID="S0kC8_NAlhQyCLKMFHHdQ"
EXPECTED_ADMIN_CHUNK="page-7ef835f4a59d5f3e.js"
EXPECTED_DASH_CHUNK="page-18c48464202db5cb.js"
EXPECTED_ABSEN_CHUNK="page-b864c4195106e108.js"
RELEASE_ID="${RELEASE_ID:-$(date +%Y%m%d_%H%M%S)}"
DRY_RUN=true
BACKUP_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) DRY_RUN=false; shift ;;
    --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

ARTIFACT_DIR="$(pwd -P)"
BACKEND_TAR="$ARTIFACT_DIR/lumin-backend-413720b1.tar.gz"
FRONTEND_TAR="$ARTIFACT_DIR/lumin-frontend-6e3a3e20.tar.gz"
BACKEND_SHA="b8b5d890f2e0d6dfdf225740e561b9c6b2ef6854f0416c96e283936ef334bba5"
FRONTEND_SHA="f96bf8805f19b555fb6f3509e57c3bc241f3cbeaa0a54b175a90f1fc71ec5740"

# ── FIXTURE MODE (tests only; loud + explicit) ──
FIXTURE_MODE=false
if [ -n "${LUMIN_FIXTURE_ROOT:-}" ]; then
  FIXTURE_MODE=true
  APP_DIR="$LUMIN_FIXTURE_ROOT/app"
  RELEASES_DIR="$APP_DIR/releases"
  EXPECTED_HOSTNAME="$(hostname)"   # fixture: accept current host
  # fixture: recompute expected SHAs from the fixture artifacts (test-only)
  _fx_dir="$(pwd -P)"
  [ -f "$_fx_dir/lumin-backend-413720b1.tar.gz" ] && BACKEND_SHA="$(sha256sum "$_fx_dir/lumin-backend-413720b1.tar.gz" | awk '{print $1}')"
  [ -f "$_fx_dir/lumin-frontend-6e3a3e20.tar.gz" ] && FRONTEND_SHA="$(sha256sum "$_fx_dir/lumin-frontend-6e3a3e20.tar.gz" | awk '{print $1}')"
  # fixture: read expected BUILD_ID/chunks from the ARTIFACT (not the live release)
  if [ -f "$_fx_dir/lumin-frontend-6e3a3e20.tar.gz" ]; then
    _fx_tmp="$(mktemp -d)"
    if tar xzf "$_fx_dir/lumin-frontend-6e3a3e20.tar.gz" -C "$_fx_tmp" 2>/dev/null; then
      [ -f "$_fx_tmp/.next/BUILD_ID" ] && EXPECTED_BUILD_ID="$(cat "$_fx_tmp/.next/BUILD_ID")"
      _fx_ac=$(find "$_fx_tmp/.next/static/chunks/app/admin" -maxdepth 1 -name "page-*.js" -printf "%f\n" 2>/dev/null | head -1)
      _fx_dc=$(find "$_fx_tmp/.next/static/chunks/app/dashboard" -maxdepth 1 -name "page-*.js" -printf "%f\n" 2>/dev/null | head -1)
      _fx_bc=$(find "$_fx_tmp/.next/static/chunks/app/absen" -maxdepth 1 -name "page-*.js" -printf "%f\n" 2>/dev/null | head -1)
      [ -n "$_fx_ac" ] && EXPECTED_ADMIN_CHUNK="$_fx_ac"
      [ -n "$_fx_dc" ] && EXPECTED_DASH_CHUNK="$_fx_dc"
      [ -n "$_fx_bc" ] && EXPECTED_ABSEN_CHUNK="$_fx_bc"
    fi
    rm -rf "$_fx_tmp" 2>/dev/null || true
  fi
  echo "############################################################"
  echo "# FIXTURE MODE ACTIVE — NOT PRODUCTION                        #"
  echo "# LUMIN_FIXTURE_ROOT=$LUMIN_FIXTURE_ROOT"
  echo "# APP_DIR=$APP_DIR"
  echo "############################################################"
fi

# ── STATE MACHINE ──
STATE="PRE_SWITCH"
RELEASE_PAIR=""
OLD_BACKEND=""
OLD_FRONTEND=""
FAILED_RELEASE=""
STAGING_DIR=""
RECOVERY_ARMED=false
PERSISTENT_PATHS=()

fatal_preflight() { echo ""; echo "FATAL (preflight — no mutation occurred): $1"; exit 1; }

recover() {
  # State-aware paired recovery. Never deletes anything.
  echo ""
  echo "=== RECOVERY (state=$STATE) ==="
  case "$STATE" in
    PRE_SWITCH)
      echo "No mutation occurred. Nothing to recover."
      ;;
    SERVICES_STOPPED)
      echo "Services stopped, no moves yet. Restarting services..."
      $DRY_RUN || { sudo systemctl start faztrack-attendance-lumin.service 2>/dev/null || true
                    sudo systemctl start faztrack-attendance-lumin-web.service 2>/dev/null || true; }
      ;;
    BACKEND_PRESERVED|FRONTEND_PRESERVED)
      # Move whatever was preserved back, whichever side(s) moved
      echo "Partial preserve detected. Restoring moved component(s)..."
      if [ -n "$OLD_BACKEND" ] && [ -d "$OLD_BACKEND" ] && [ ! -d "$APP_DIR/backend" ]; then
        mv "$OLD_BACKEND" "$APP_DIR/backend"
        echo "  Restored backend"
      fi
      if [ -n "$OLD_FRONTEND" ] && [ -d "$OLD_FRONTEND" ] && [ ! -d "$APP_DIR/frontend" ]; then
        mv "$OLD_FRONTEND" "$APP_DIR/frontend"
        echo "  Restored frontend"
      fi
      $DRY_RUN || { sudo systemctl start faztrack-attendance-lumin.service 2>/dev/null || true
                    sudo systemctl start faztrack-attendance-lumin-web.service 2>/dev/null || true; }
      ;;
    BACKEND_ACTIVATED|FRONTEND_ACTIVATED|SERVICES_STARTED|VERIFYING)
      echo "Full/partial switch detected. Performing PAIRED rollback..."
      # Preserve failed state WITHOUT deleting
      if [ -z "$FAILED_RELEASE" ]; then
        FAILED_RELEASE="$RELEASES_DIR/failed-$RELEASE_ID-$(date +%s)"
      fi
      if [ ! -d "$FAILED_RELEASE" ]; then
        mkdir "$FAILED_RELEASE" 2>/dev/null || true
      fi
      if [ -d "$APP_DIR/backend" ]; then
        mv "$APP_DIR/backend" "$FAILED_RELEASE/backend" 2>/dev/null || true
      fi
      if [ -d "$APP_DIR/frontend" ]; then
        mv "$APP_DIR/frontend" "$FAILED_RELEASE/frontend" 2>/dev/null || true
      fi
      if [ -n "$OLD_BACKEND" ] && [ -d "$OLD_BACKEND" ]; then
        mv "$OLD_BACKEND" "$APP_DIR/backend" 2>/dev/null || true
        echo "  Restored backend from $OLD_BACKEND"
      fi
      if [ -n "$OLD_FRONTEND" ] && [ -d "$OLD_FRONTEND" ]; then
        mv "$OLD_FRONTEND" "$APP_DIR/frontend" 2>/dev/null || true
        echo "  Restored frontend from $OLD_FRONTEND"
      fi
      $DRY_RUN || { sudo systemctl start faztrack-attendance-lumin.service 2>/dev/null || true
                    sudo systemctl start faztrack-attendance-lumin-web.service 2>/dev/null || true; }
      echo ""
      echo "Recovery paths preserved (nothing deleted):"
      echo "  Failed release : ${FAILED_RELEASE:-<none>}"
      echo "  Staging        : ${STAGING_DIR:-<none>}"
      echo "  Old backend    : ${OLD_BACKEND:-<none>}"
      echo "  Old frontend   : ${OLD_FRONTEND:-<none>}"
      ;;
    VERIFIED)
      echo "State VERIFIED. No recovery needed."
      ;;
  esac
  echo "=== RECOVERY END ==="
}

fatal_after_switch() {
  echo ""
  echo "FATAL (post-mutation): $1"
  recover
  exit 1
}

# ERR handler armed only after mutation begins
on_err() {
  if $RECOVERY_ARMED; then
    echo ""
    echo "ERROR trap fired (state=$STATE)"
    recover
  fi
  exit 1
}
trap on_err ERR

# ── CHECKED MUTATING COMMANDS ──
mx() {
  # Mutation executor: dry-run prints, execute runs and aborts via fatal_after_switch
  if $DRY_RUN; then
    echo "[DRY-RUN] $*"
  else
    echo "[EXEC] $*"
    if ! "$@"; then
      fatal_after_switch "Mutation failed: $*"
    fi
  fi
}

echo "=== LUMIN PRODUCTION DEPLOY V4.1 ==="
echo "Release ID: $RELEASE_ID"
echo "Dry-run: $DRY_RUN"
echo "Artifact dir: $ARTIFACT_DIR"
echo ""

# ══════════════════════════════════════════
# PREFLIGHT (no mutation — fatal_preflight only)
# ══════════════════════════════════════════
[ "$(hostname)" = "$EXPECTED_HOSTNAME" ] || fatal_preflight "Hostname mismatch: $(hostname) != $EXPECTED_HOSTNAME"
[ "$(whoami)" = "$EXPECTED_USER" ] || $FIXTURE_MODE || fatal_preflight "Must run as $EXPECTED_USER, got $(whoami)"
[ -d "$APP_DIR" ] || fatal_preflight "App dir not found: $APP_DIR"

# ── Backup validation ──
echo "--- Backup Validation ---"
[ -n "$BACKUP_DIR" ] || fatal_preflight "Missing --backup-dir argument"
[ -d "$BACKUP_DIR" ] || fatal_preflight "Backup directory not found: $BACKUP_DIR"
[ -f "$BACKUP_DIR/BACKUP-MANIFEST.md" ] || fatal_preflight "BACKUP-MANIFEST.md not found"
[ -f "$BACKUP_DIR/checksums-sha256.txt" ] || fatal_preflight "checksums-sha256.txt not found (mandatory)"
[ -f "$BACKUP_DIR/VERIFICATION-RESULT.txt" ] || fatal_preflight "VERIFICATION-RESULT.txt not found"

BH=$(grep "^Hostname:" "$BACKUP_DIR/BACKUP-MANIFEST.md" | awk '{print $2}')
[ "$BH" = "$(hostname)" ] || fatal_preflight "Backup hostname mismatch: $BH != $(hostname)"
grep -q "Validation Status: PASS" "$BACKUP_DIR/BACKUP-MANIFEST.md" || fatal_preflight "Backup status not PASS"

if ! ( cd "$BACKUP_DIR" && sha256sum -c checksums-sha256.txt > /dev/null 2>&1 ); then
  fatal_preflight "Backup checksum verification failed"
fi
echo "  Checksums: PASS"

DUMPS=$(find "$BACKUP_DIR" -maxdepth 1 -name "db-dump-*.dump" | wc -l)
[ "$DUMPS" -eq 1 ] || fatal_preflight "Expected exactly 1 dump, found $DUMPS"
DUMP_FILE=$(find "$BACKUP_DIR" -maxdepth 1 -name "db-dump-*.dump" | head -1)
[ "$(stat -c%s "$DUMP_FILE")" -gt 0 ] || fatal_preflight "Dump is empty"
if ! docker exec -i "$PG_CONTAINER" pg_restore --list < "$DUMP_FILE" > /dev/null 2>&1; then
  fatal_preflight "pg_restore --list failed"
fi
echo "  Backup: PASS"

# ── Artifact validation ──
echo "--- Artifact Verification ---"
[ -f "$BACKEND_TAR" ] || fatal_preflight "Backend artifact not found: $BACKEND_TAR"
[ -f "$FRONTEND_TAR" ] || fatal_preflight "Frontend artifact not found: $FRONTEND_TAR"
[ "$(sha256sum "$BACKEND_TAR" | awk '{print $1}')" = "$BACKEND_SHA" ] || fatal_preflight "Backend SHA256 mismatch"
[ "$(sha256sum "$FRONTEND_TAR" | awk '{print $1}')" = "$FRONTEND_SHA" ] || fatal_preflight "Frontend SHA256 mismatch"
echo "  Artifacts: PASS"

# ── Persistent inventory (BEFORE staging) ──
echo "--- Persistent Inventory ---"
for p in "$APP_DIR/uploads" "$APP_DIR/runtime" "$APP_DIR/persistent" \
         "$APP_DIR/backend/uploads" "$APP_DIR/backend/runtime" "$APP_DIR/backend/persistent"; do
  if [ -d "$p" ]; then
    PERSISTENT_PATHS+=("$p")
    echo "  Found: $p ($(find "$p" -type f | wc -l) files, owner $(stat -c '%U:%G' "$p"))"
  fi
done
[ "${#PERSISTENT_PATHS[@]}" -eq 0 ] && echo "  (none present)"

# ── Path safety ──
echo "--- Path Safety ---"
STAGING_DIR="$APP_DIR/staging-$RELEASE_ID"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RELEASE_PAIR="$APP_DIR/releases/release-pair-$TIMESTAMP"
OLD_BACKEND="$RELEASE_PAIR/backend-old"
OLD_FRONTEND="$RELEASE_PAIR/frontend-old"

[ -d "$STAGING_DIR" ] && fatal_preflight "Staging already exists: $STAGING_DIR"
[ -d "$RELEASE_PAIR" ] && fatal_preflight "Release pair already exists: $RELEASE_PAIR"
echo "  Paths free."

# ══════════════════════════════════════════
# STAGING (mutation begins)
# ══════════════════════════════════════════
echo ""
echo "--- Staging ---"
if $DRY_RUN; then
  echo "[DRY-RUN] Would create staging: $STAGING_DIR"
else
  RECOVERY_ARMED=true
  STATE="STAGING"
  mkdir "$STAGING_DIR" || fatal_after_switch "Cannot create staging dir"
fi

mx mkdir "$STAGING_DIR/backend"
mx tar xzf "$BACKEND_TAR" -C "$STAGING_DIR/backend" --strip-components=1
# Ensure no .venv came from the artifact (venv must come from current release)
if [ -d "$STAGING_DIR/backend/.venv" ]; then
  # Artifact unexpectedly shipped a .venv — move it aside (never delete)
  _venv_artifact_backup="$STAGING_DIR/artifact-venv-unexpected"
  mx mv "$STAGING_DIR/backend/.venv" "$_venv_artifact_backup"
  echo "  NOTE: artifact contained .venv; preserved at $_venv_artifact_backup"
fi
mx cp -a "$APP_DIR/backend/.venv" "$STAGING_DIR/backend/.venv"
mx cp "$APP_DIR/backend/.env.lumin" "$STAGING_DIR/backend/.env.lumin"

# Copy backend persistent dirs into staged backend at same relative path
for p in "$APP_DIR/backend/uploads" "$APP_DIR/backend/runtime" "$APP_DIR/backend/persistent"; do
  if [ -d "$p" ]; then
    rel="${p#"$APP_DIR"/backend/}"
    mx cp -a "$p" "$STAGING_DIR/backend/$rel"
    echo "  Staged persistent: backend/$rel"
  fi
done

mx mkdir "$STAGING_DIR/frontend"
mx tar xzf "$FRONTEND_TAR" -C "$STAGING_DIR/frontend"
[ -f "$APP_DIR/frontend/.env.local" ] && mx cp "$APP_DIR/frontend/.env.local" "$STAGING_DIR/frontend/.env.local"

# ── Validate staged ──
echo "--- Staged Validation ---"
if ! $DRY_RUN; then
  [ -x "$STAGING_DIR/backend/.venv/bin/uvicorn" ] || fatal_after_switch "uvicorn not executable"
  [ -f "$STAGING_DIR/backend/.env.lumin" ] || fatal_after_switch ".env.lumin missing"
  [ -f "$STAGING_DIR/backend/app/main.py" ] || fatal_after_switch "main.py missing"
  [ -f "$STAGING_DIR/frontend/.next/standalone/server.js" ] || fatal_after_switch "server.js missing"
  [ -d "$STAGING_DIR/frontend/.next/static" ] || fatal_after_switch ".next/static missing"
  STAGED_BUILD=$(cat "$STAGING_DIR/frontend/.next/BUILD_ID" 2>/dev/null || echo "N/A")
  [ "$STAGED_BUILD" = "$EXPECTED_BUILD_ID" ] || fatal_after_switch "BUILD_ID mismatch: $STAGED_BUILD"
  echo "  Staged: PASS"
fi

# ── Pre-switch health ──
echo "--- Pre-switch Health ---"
if ! $DRY_RUN; then
  curl -sf http://localhost:8011/health/live > /dev/null 2>&1 || fatal_after_switch "Backend unhealthy pre-switch"
  [ "$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login)" = "200" ] || fatal_after_switch "Frontend unhealthy pre-switch"
  echo "  Health: PASS"
fi

# ══════════════════════════════════════════
# PAIRED SWITCH
# ══════════════════════════════════════════
echo ""
echo "--- Paired Switch ---"
# Parent may already exist (idempotent); release-pair must NOT exist
[ -d "$RELEASES_DIR" ] || mx mkdir "$RELEASES_DIR"
mx mkdir "$RELEASE_PAIR"

STATE="SERVICES_STOPPED"
mx sudo systemctl stop faztrack-attendance-lumin.service
mx sudo systemctl stop faztrack-attendance-lumin-web.service

STATE="BACKEND_PRESERVED"
mx mv "$APP_DIR/backend" "$OLD_BACKEND"

STATE="FRONTEND_PRESERVED"
mx mv "$APP_DIR/frontend" "$OLD_FRONTEND"

STATE="BACKEND_ACTIVATED"
mx mv "$STAGING_DIR/backend" "$APP_DIR/backend"

STATE="FRONTEND_ACTIVATED"
mx mv "$STAGING_DIR/frontend" "$APP_DIR/frontend"

STATE="SERVICES_STARTED"
mx sudo systemctl start faztrack-attendance-lumin.service
mx sudo systemctl start faztrack-attendance-lumin-web.service
$DRY_RUN || sleep 3

# ══════════════════════════════════════════
# POST-DEPLOY VERIFICATION (state=VERIFYING)
# ══════════════════════════════════════════
STATE="VERIFYING"
echo ""
echo "--- Post-deploy Verification ---"

if ! $DRY_RUN; then
  [ "$(systemctl is-active faztrack-attendance-lumin.service)" = "active" ] || fatal_after_switch "Backend service not active"
  [ "$(systemctl is-active faztrack-attendance-lumin-web.service)" = "active" ] || fatal_after_switch "Frontend service not active"
  echo "  systemd: PASS"

  curl -sf http://localhost:8011/health/live > /dev/null 2>&1 || fatal_after_switch "Backend health failed"
  [ "$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3011/login)" = "200" ] || fatal_after_switch "Local frontend /login failed"
  echo "  local health: PASS"

  [ "$(curl -sf -o /dev/null -w '%{http_code}' https://attendance-lumin.gofaztrack.com/login)" = "200" ] || fatal_after_switch "Public /login failed"
  [ "$(curl -sf -o /dev/null -w '%{http_code}' https://attendance-lumin.gofaztrack.com/absen)" = "200" ] || fatal_after_switch "Public /absen failed"
  echo "  public health: PASS"

  [ "$(cat "$APP_DIR/frontend/.next/BUILD_ID")" = "$EXPECTED_BUILD_ID" ] || fatal_after_switch "BUILD_ID mismatch"
  echo "  BUILD_ID: PASS"

  AC=$(find "$APP_DIR/frontend/.next/static/chunks/app/admin" -maxdepth 1 -name "page-*.js" -printf "%f\n" 2>/dev/null | head -1)
  DC=$(find "$APP_DIR/frontend/.next/static/chunks/app/dashboard" -maxdepth 1 -name "page-*.js" -printf "%f\n" 2>/dev/null | head -1)
  BC=$(find "$APP_DIR/frontend/.next/static/chunks/app/absen" -maxdepth 1 -name "page-*.js" -printf "%f\n" 2>/dev/null | head -1)
  [ "$AC" = "$EXPECTED_ADMIN_CHUNK" ] || fatal_after_switch "Admin chunk mismatch: $AC"
  [ "$DC" = "$EXPECTED_DASH_CHUNK" ] || fatal_after_switch "Dashboard chunk mismatch: $DC"
  [ "$BC" = "$EXPECTED_ABSEN_CHUNK" ] || fatal_after_switch "Absen chunk mismatch: $BC"
  echo "  chunks: PASS"

  # Persistent integrity (post-switch)
  for p in "${PERSISTENT_PATHS[@]}"; do
    if [ ! -e "$p" ]; then
      fatal_after_switch "Persistent path missing after switch: $p"
    fi
  done
  echo "  persistent: PASS (${#PERSISTENT_PATHS[@]} paths verified)"
fi

# ══════════════════════════════════════════
# VERIFIED (disable recovery)
# ══════════════════════════════════════════
STATE="VERIFIED"
RECOVERY_ARMED=false

echo ""
echo "=== DEPLOY COMPLETE (VERIFIED) ==="
echo "Release: $RELEASE_ID"
echo "Release pair: $RELEASE_PAIR"
echo "Old backend : $OLD_BACKEND"
echo "Old frontend: $OLD_FRONTEND"
echo "Estimated downtime: ~10 seconds"
echo "Rollback: bash lumin-prod-rollback.sh --execute --release-pair $RELEASE_PAIR"
