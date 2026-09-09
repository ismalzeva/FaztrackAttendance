#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-discovery-v2.sh
# READ-ONLY production discovery for Lumin Attendance
# Production: /home/ubuntu/apps/attendance-lumin/
# DB: attendance-lumin-postgres | Caddy: caddy-main
# ─────────────────────────────────────────────────────────
set -euo pipefail
export LC_ALL=C

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
OK=0; PARTIAL=0; BLOCKED=0

section() { echo ""; echo "════════════════════════════════════════"; echo "  $1"; echo "════════════════════════════════════════"; }
mark() {
  local label="$1" value="$2" expect="${3:-}"
  if [ -z "$value" ] || [ "$value" = "N/A" ] || [ "$value" = "(not found)" ] || [ "$value" = "(not listening)" ]; then
    echo "  $label: $value  [BLOCKED]"; BLOCKED=$((BLOCKED+1))
  elif [ -n "$expect" ] && [[ "$value" == ${expect}* ]]; then
    echo "  $label: $value  [PASS]"; OK=$((OK+1))
  elif [ -n "$expect" ]; then
    echo "  $label: $value  [PARTIAL]"; PARTIAL=$((PARTIAL+1))
  else
    echo "  $label: $value"; OK=$((OK+1))
  fi
}

# ── PRODUCTION PATHS ──
APP_DIR="/home/ubuntu/apps/attendance-lumin"
BE_DIR="$APP_DIR/backend"
FE_DIR="$APP_DIR/frontend"
PG_CONTAINER="attendance-lumin-postgres"
CADDY_CONTAINER="caddy-main"

echo "LUMIN PRODUCTION DISCOVERY V2 — $TS"
echo "Script: lumin-prod-discovery-v2.sh (read-only)"

# ══════════════════════════════════════════
# 1. APPLICATION
# ══════════════════════════════════════════
section "1. APPLICATION"

# Frontend BUILD_ID
if [ -f "$FE_DIR/.next/BUILD_ID" ]; then
  mark "frontend_build_id" "$(cat "$FE_DIR/.next/BUILD_ID" 2>/dev/null || echo N/A)"
else
  mark "frontend_build_id" "(not found)"
fi

# package.json name/version
if [ -f "$FE_DIR/package.json" ]; then
  pname=$(grep -oP '"name"\s*:\s*"\K[^"]+' "$FE_DIR/package.json" 2>/dev/null || echo N/A)
  pver=$(grep -oP '"version"\s*:\s*"\K[^"]+' "$FE_DIR/package.json" 2>/dev/null || echo N/A)
  mark "frontend_package" "$pname v$pver"
else
  mark "frontend_package" "(not found)"
fi

if [ -f "$BE_DIR/package.json" ]; then
  bname=$(grep -oP '"name"\s*:\s*"\K[^"]+' "$BE_DIR/package.json" 2>/dev/null || echo N/A)
  bver=$(grep -oP '"version"\s*:\s*"\K[^"]+' "$BE_DIR/package.json" 2>/dev/null || echo N/A)
  mark "backend_package" "$bname v$bver"
fi

# Folder sizes
mark "backend_size" "$(du -sh "$BE_DIR" 2>/dev/null | cut -f1 || echo N/A)"
mark "frontend_size" "$(du -sh "$FE_DIR" 2>/dev/null | cut -f1 || echo N/A)"

# Key file checksums (files that will change during deployment)
echo ""
echo "--- checksums (deployment target files) ---"
for f in "$BE_DIR/app/main.py" "$BE_DIR/app/worker_web.py" "$BE_DIR/app/admin_management.py" "$BE_DIR/app/schedule_management.py" "$BE_DIR/app/leave_management.py" "$BE_DIR/app/phase3.py" "$BE_DIR/app/lumin_dashboard.py"; do
  if [ -f "$f" ]; then
    mark "$(basename "$f")" "$(sha256sum "$f" 2>/dev/null | cut -c1-16 || echo N/A)"
  else
    mark "$(basename "$f")" "(not found)"
  fi
done

# Timestamps
echo ""
echo "--- key timestamps ---"
mark "backend_main_ts" "$(stat -c '%Y %y' "$BE_DIR/app/main.py" 2>/dev/null | cut -d' ' -f1-2 || echo N/A)"
mark "frontend_build_ts" "$(stat -c '%Y %y' "$FE_DIR/.next/BUILD_ID" 2>/dev/null | cut -d' ' -f1-2 || echo N/A)"

# ══════════════════════════════════════════
# 2. SERVICES
# ══════════════════════════════════════════
section "2. SERVICES"
for svc in faztrack-attendance-lumin.service faztrack-attendance-lumin-web.service; do
  echo "--- $svc ---"
  f="/etc/systemd/system/$svc"
  if [ -f "$f" ]; then
    mark "config" "$f"
    mark "WorkingDirectory" "$(grep -E "^WorkingDirectory=" "$f" 2>/dev/null | head -1 | cut -d= -f2 || echo N/A)"
    exec_line=$(grep -E "^ExecStart=" "$f" 2>/dev/null | head -1 | cut -d= -f2 || true)
    mark "executable" "$(echo "$exec_line" | awk '{print $1}' || echo N/A)"
    mark "user" "$(grep -E "^User=" "$f" 2>/dev/null | head -1 | cut -d= -f2 || echo N/A)"
    mark "group" "$(grep -E "^Group=" "$f" 2>/dev/null | head -1 | cut -d= -f2 || echo N/A)"
    mark "restart" "$(grep -E "^Restart=" "$f" 2>/dev/null | head -1 | cut -d= -f2 || echo N/A)"
    envfiles=$(grep -E "^EnvironmentFile=" "$f" 2>/dev/null | cut -d= -f2 || true)
    mark "environment_file" "${envfiles:-(none)}"
    # Service status
    mark "status" "$(systemctl is-active "$svc" 2>/dev/null || echo N/A)"
  else
    echo "  (not found)"
  fi
done

# ══════════════════════════════════════════
# 3. CADDY CONTAINER
# ══════════════════════════════════════════
section "3. CADDY CONTAINER"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CADDY_CONTAINER"; then
  mark "container" "$CADDY_CONTAINER"
  mark "image" "$(docker inspect "$CADDY_CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || echo N/A)"
  mark "network" "$(docker inspect "$CADDY_CONTAINER" --format '{{.HostConfig.NetworkMode}}' 2>/dev/null || echo N/A)"
  mark "ports" "$(docker port "$CADDY_CONTAINER" 2>/dev/null | tr '\n' ' ' || echo N/A)"
  mark "caddy_version" "$(docker exec "$CADDY_CONTAINER" caddy version 2>/dev/null || echo N/A)"
  # Config mount
  mark "config_mount" "$(docker inspect "$CADDY_CONTAINER" --format '{{range .Mounts}}{{.Source}}->{{.Destination}} {{end}}' 2>/dev/null | grep -i caddy | head -1 || echo N/A)"
  # Caddyfile content — only domain + upstream, redact secrets
  echo ""
  echo "--- caddy config (attendance-lumin) ---"
  caddy_cfg=$(docker exec "$CADDY_CONTAINER" cat /etc/caddy/Caddyfile 2>/dev/null || echo "")
  if [ -n "$caddy_cfg" ]; then
    in_block=0
    echo "$caddy_cfg" | while IFS= read -r line; do
      if echo "$line" | grep -q "attendance-lumin.gofaztrack.com"; then
        in_block=1; echo "  domain: attendance-lumin.gofaztrack.com"; continue
      fi
      if [ "$in_block" = "1" ]; then
        echo "$line" | grep -q "^}" && { in_block=0; continue; }
        # Redact sensitive
        if echo "$line" | grep -iqE "password|token|secret|authorization|cookie|credential|header_up|key"; then
          echo "  [REDACTED]"; continue
        fi
        # Show upstreams only
        if echo "$line" | grep -q "reverse_proxy"; then
          upstream=$(echo "$line" | sed 's/.*reverse_proxy//' | awk '{print $1}')
          echo "  upstream: $upstream"
        fi
      fi
    done
  else
    echo "  (cannot read Caddyfile)"
  fi
else
  echo "  Container '$CADDY_CONTAINER' not found."
  echo "  Candidates:"
  docker ps --format '  {{.Names}}' 2>/dev/null | grep -i caddy || echo "  (none)"
fi

# ══════════════════════════════════════════
# 4. DATABASE
# ══════════════════════════════════════════
section "4. DATABASE"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$PG_CONTAINER"; then
  mark "container" "$PG_CONTAINER"
  mark "image" "$(docker inspect "$PG_CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || echo N/A)"
  # Get POSTGRES_USER and POSTGRES_DB from env (don't print values)
  pg_user=$(docker exec "$PG_CONTAINER" printenv POSTGRES_USER 2>/dev/null || echo "postgres")
  pg_db=$(docker exec "$PG_CONTAINER" printenv POSTGRES_DB 2>/dev/null || echo "postgres")
  mark "pg_user" "(from container env, not displayed)"
  mark "pg_db" "(from container env, not displayed)"
  # PostgreSQL version
  mark "pg_version" "$(docker exec "$PG_CONTAINER" psql -U "$pg_user" -d "$pg_db" -t -c "SELECT version();" 2>/dev/null | head -1 | cut -d' ' -f1-2 || echo N/A)"
  # Table count
  mark "tables_public" "$(docker exec "$PG_CONTAINER" psql -U "$pg_user" -d "$pg_db" -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null | tr -d ' ' || echo N/A)"
  # Row counts for main tables
  for tbl in workers projects attendance_events leave_requests schedule_templates employee_schedules holiday_calendar overtime_requests attendance_corrections; do
    cnt=$(docker exec "$PG_CONTAINER" psql -U "$pg_user" -d "$pg_db" -t -c "SELECT count(*) FROM $tbl" 2>/dev/null | tr -d ' ' || echo "N/A")
    mark "rows_$tbl" "$cnt"
  done
  # Database size
  mark "db_size" "$(docker exec "$PG_CONTAINER" psql -U "$pg_user" -d "$pg_db" -t -c "SELECT pg_size_pretty(pg_database_size('$pg_db'))" 2>/dev/null | tr -d ' ' || echo N/A)"
  # Health
  mark "pg_health" "$(docker exec "$PG_CONTAINER" pg_isready -U "$pg_user" 2>/dev/null || echo N/A)"
  # Volume
  mark "volume" "$(docker inspect "$PG_CONTAINER" --format '{{range .Mounts}}{{.Source}}->{{.Destination}} {{end}}' 2>/dev/null | head -1 || echo N/A)"
else
  echo "  Container '$PG_CONTAINER' not found."
  echo "  Candidates:"
  docker ps --format '  {{.Names}}' 2>/dev/null | grep -i "postgres\|lumin" || echo "  (none)"
  echo "  SKIP database queries."
fi

# ══════════════════════════════════════════
# 5. BACKUP AND RELEASE
# ══════════════════════════════════════════
section "5. BACKUP AND RELEASE"
# Existing backup locations
echo "--- backup locations ---"
for d in /home/ubuntu/backups "$APP_DIR/backups" "$BE_DIR/backups" "$FE_DIR/backups"; do
  if [ -d "$d" ]; then
    mark "backup_dir" "$d ($(find "$d" -maxdepth 1 2>/dev/null | wc -l) files, $(du -sh "$d" 2>/dev/null | cut -f1 || echo N/A))"
  fi
done
[ -d /home/ubuntu/backups ] || mark "backup_dir" "/home/ubuntu/backups (not found)"

# Filesystem mounts
echo ""
echo "--- mounts ---"
mark "app_mount" "$(df -h "$APP_DIR" 2>/dev/null | tail -1 | awk '{print $1 " " $2 " " $4 " avail"}' || echo N/A)"

# Available disk
mark "disk_avail" "$(df -h / 2>/dev/null | tail -1 | awk '{print $4}' || echo N/A)"

# Release folders / symlinks
echo ""
echo "--- release structure ---"
for d in "$APP_DIR/release" "$APP_DIR/current" "$APP_DIR/releases"; do
  if [ -d "$d" ] || [ -L "$d" ]; then
    mark "release_path" "$d EXISTS ($(find "$d" -maxdepth 1 2>/dev/null | wc -l) items)"
  fi
done
# Check if app dir itself is a symlink
if [ -L "$APP_DIR" ]; then
  mark "app_symlink" "$APP_DIR -> $(readlink "$APP_DIR")"
else
  mark "app_symlink" "$APP_DIR (not a symlink)"
fi

# ══════════════════════════════════════════
# 6. DEPLOYMENT RECOMMENDATION
# ══════════════════════════════════════════
section "6. DEPLOYMENT RECOMMENDATION"
cat << 'EOF'
  Production is NOT a git repository.
  Recommended: Immutable Release Bundle approach.

  1. BUILD on dev (43.134.112.7):
     - Backend: tar.gz from git archive lumin-park SHA
     - Frontend: tar.gz of .next/standalone + public (pre-built)

  2. BACKUP on production:
     - cp -a $APP_DIR/backend $APP_DIR/backups/backend-<timestamp>
     - cp -a $APP_DIR/frontend $APP_DIR/backups/frontend-<timestamp>
     - pg_dump attendance-lumin-postgres > backups/db-<timestamp>.sql

  3. DEPLOY (atomic switch):
     - Extract new release to $APP_DIR/backend-new / frontend-new
     - Health check on temp ports
     - Stop old services
     - Rename: backend -> backend-old, backend-new -> backend
     - Start new services
     - Health check on production ports

  4. ROLLBACK:
     - Stop new services
     - Rename: backend -> backend-failed, backend-old -> backend
     - Start old services
     - Restore DB if needed from backup

  DO NOT: git pull, git reset --hard, build on production
EOF

# ══════════════════════════════════════════
# 7. SUMMARY
# ══════════════════════════════════════════
section "7. SUMMARY"
TOTAL=$((OK + PARTIAL + BLOCKED))
echo "timestamp: $TS"
echo "script: lumin-prod-discovery-v2.sh (read-only)"
echo "checks_ok: $OK"
echo "checks_partial: $PARTIAL"
echo "checks_blocked: $BLOCKED"
echo "checks_total: $TOTAL"
if [ "$BLOCKED" -gt 0 ]; then
  echo "verdict: BLOCKED ($BLOCKED checks blocked)"
elif [ "$PARTIAL" -gt 0 ]; then
  echo "verdict: PARTIAL ($PARTIAL checks partial)"
else
  echo "verdict: PASS (all checks passed)"
fi
echo ""
echo "SECURITY: No passwords, tokens, keys, connection strings, or secrets displayed."
echo "SECURITY: No files, services, databases, or DNS modified."
echo "SECURITY: No restart, deploy, or mutation performed."
