#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-preflight.sh
# READ-ONLY production preflight for Lumin Attendance
# Runs on 43.163.7.128 — does NOT modify anything
# Security: no secrets, no passwords, no tokens displayed
# ─────────────────────────────────────────────────────────
set -euo pipefail
export LC_ALL=C

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SECTION_OK=0
SECTION_PARTIAL=0
SECTION_BLOCKED=0

section() { echo ""; echo "════════════════════════════════════════"; echo "  $1"; echo "════════════════════════════════════════"; }

mark() {
  # $1=label $2=value $3=expected_prefix
  local label="$1" value="$2" expect="${3:-}"
  if [ -z "$value" ] || [ "$value" = "N/A" ] || [ "$value" = "(not found)" ] || [ "$value" = "(not listening)" ]; then
    echo "  $label: $value  [BLOCKED]"
    SECTION_BLOCKED=$((SECTION_BLOCKED + 1))
  elif [ -n "$expect" ] && [[ "$value" == ${expect}* ]]; then
    echo "  $label: $value  [PASS]"
    SECTION_OK=$((SECTION_OK + 1))
  elif [ -n "$expect" ]; then
    echo "  $label: $value  [PARTIAL]"
    SECTION_PARTIAL=$((SECTION_PARTIAL + 1))
  else
    echo "  $label: $value"
    SECTION_OK=$((SECTION_OK + 1))
  fi
}

redact_line() {
  # Redact lines containing sensitive keywords
  grep -iE "password|token|secret|authorization|cookie|key|credential|header_up" 2>/dev/null | sed 's/.*/  [REDACTED]/' || true
}

echo "LUMIN PRODUCTION PREFLIGHT — $TS"
echo "Script: lumin-prod-preflight.sh (read-only, no modifications)"

# ── 1. SYSTEM ──
section "1. SYSTEM"
mark "hostname" "$(hostname 2>/dev/null || echo N/A)"
mark "os" "$(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' || echo N/A)"
mark "kernel" "$(uname -r 2>/dev/null || echo N/A)"
mark "disk" "$(df -h / 2>/dev/null | tail -1 | awk '{print $2 " total, " $3 " used, " $4 " avail"}' || echo N/A)"
mark "ram" "$(free -h 2>/dev/null | awk '/Mem:/{print $2 " total, " $3 " used, " $7 " available"}' || echo N/A)"
mark "user" "$(whoami 2>/dev/null || echo N/A)"
mark "uptime" "$(uptime -p 2>/dev/null || echo N/A)"

# ── 2. SERVICES ──
section "2. SERVICES"
echo "--- systemd lumin services ---"
systemctl list-units --type=service --state=running 2>/dev/null | grep -i "lumin\|faztrack" || echo "  (none found)"
echo ""
echo "--- docker containers ---"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null | grep -i "lumin\|postgres\|caddy" || echo "  (none found)"

# ── 3. PORTS ──
section "3. PORTS"
for port in 3011 8011 5437 80 443; do
  pid=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
  if [ -n "$pid" ]; then
    comm=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
    mark "port $port" "pid=$pid comm=$comm"
  else
    mark "port $port" "(not listening)"
  fi
done

# ── 4. REPOSITORY ──
section "4. REPOSITORY"
for dir in /home/ubuntu/lumin-frontend /home/ubuntu/FaztrackAttendance; do
  echo "--- $dir ---"
  if [ -d "$dir/.git" ]; then
    cd "$dir"
    mark "branch" "$(git branch --show-current 2>/dev/null || echo N/A)"
    mark "sha" "$(git rev-parse HEAD 2>/dev/null || echo N/A)"
    mark "commit" "$(git log --oneline -1 2>/dev/null || echo N/A)"
    mark "dirty" "$(git status --short 2>/dev/null | wc -l | tr -d ' ') files"
  else
    echo "  (not a git repository)"
  fi
done

# ── 5. SERVICE CONFIG ──
section "5. SERVICE CONFIG"
for svc in faztrack-attendance-lumin.service faztrack-attendance-lumin-web.service; do
  f="/etc/systemd/system/$svc"
  echo "--- $svc ---"
  if [ -f "$f" ]; then
    mark "config" "$f EXISTS"
    # WorkingDirectory
    wd=$(grep -E "^WorkingDirectory=" "$f" 2>/dev/null | head -1 | cut -d= -f2 || true)
    [ -n "$wd" ] && mark "WorkingDirectory" "$wd"
    # ExecStart (path only, no args)
    exec_line=$(grep -E "^ExecStart=" "$f" 2>/dev/null | head -1 | cut -d= -f2 || true)
    exec_path=$(echo "$exec_line" | awk '{print $1}')
    [ -n "$exec_path" ] && mark "ExecStart" "$exec_path"
    # EnvironmentFile (path only)
    envfiles=$(grep -E "^EnvironmentFile=" "$f" 2>/dev/null | cut -d= -f2 || true)
    [ -n "$envfiles" ] && mark "EnvironmentFile" "$envfiles"
    # Do NOT display Environment= values
  else
    echo "  (not found)"
  fi
done

# ── 6. CADDY ──
section "6. CADDY"
echo "--- Caddy config (attendance-lumin) ---"
if [ -f /etc/caddy/Caddyfile ]; then
  # Show only domain and reverse_proxy upstream, redact sensitive lines
  in_block=0
  while IFS= read -r line; do
    if echo "$line" | grep -q "attendance-lumin.gofaztrack.com"; then
      in_block=1
      echo "  domain: attendance-lumin.gofaztrack.com"
      continue
    fi
    if [ "$in_block" = "1" ]; then
      if echo "$line" | grep -q "^}"; then
        in_block=0
        continue
      fi
      # Redact sensitive lines
      if echo "$line" | grep -iqE "password|token|secret|authorization|cookie|key|credential|header_up"; then
        echo "  [REDACTED]"
        continue
      fi
      # Show reverse_proxy upstreams
      if echo "$line" | grep -q "reverse_proxy"; then
        upstream=$(echo "$line" | sed 's/.*reverse_proxy//' | awk '{print $1}')
        echo "  upstream: $upstream"
      fi
    fi
  done < /etc/caddy/Caddyfile
else
  echo "  (Caddyfile not found)"
fi
echo ""
mark "caddy_version" "$(caddy version 2>/dev/null || echo N/A)"

# ── 7. DATABASE ──
section "7. DATABASE"
PG_CONTAINER="lumin-postgres"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$PG_CONTAINER"; then
  mark "container" "$PG_CONTAINER"
  mark "image" "$(docker inspect "$PG_CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || echo N/A)"
  mark "tables" "$(docker exec "$PG_CONTAINER" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null | tr -d ' ' || echo N/A)"
  mark "workers" "$(docker exec "$PG_CONTAINER" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM workers WHERE tenant_id='lumin-park-001' AND is_active=true" 2>/dev/null | tr -d ' ' || echo N/A)"
  mark "projects" "$(docker exec "$PG_CONTAINER" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM projects WHERE tenant_id='lumin-park-001'" 2>/dev/null | tr -d ' ' || echo N/A)"
  mark "attendance_events" "$(docker exec "$PG_CONTAINER" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM attendance_events" 2>/dev/null | tr -d ' ' || echo N/A)"
  mark "templates" "$(docker exec "$PG_CONTAINER" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM schedule_templates WHERE tenant_id='lumin-park-001'" 2>/dev/null | tr -d ' ' || echo N/A)"
  mark "holidays" "$(docker exec "$PG_CONTAINER" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM holiday_calendar WHERE tenant_id='lumin-park-001'" 2>/dev/null | tr -d ' ' || echo N/A)"
else
  echo "  Container '$PG_CONTAINER' not found. Candidates:"
  docker ps --format '  {{.Names}}' 2>/dev/null | grep -i "postgres\|lumin" || echo "  (none)"
  echo "  SKIP database queries."
fi

# ── 8. CONFIG LOCATIONS ──
section "8. CONFIG LOCATIONS"
for f in /home/ubuntu/FaztrackAttendance/backend/.env.lumin /home/ubuntu/lumin-frontend/.env.local; do
  if [ -f "$f" ]; then
    mark "env_file" "$f EXISTS ($(wc -l < "$f") lines, contents REDACTED)"
  else
    mark "env_file" "$f NOT FOUND"
  fi
done

# ── 9. HEALTH ──
section "9. HEALTH CHECKS"
mark "backend_health" "$(curl -sf http://localhost:8011/health/live 2>/dev/null || echo 'NOT RESPONDING')"
mark "frontend_health" "$(curl -sf -o /dev/null -w 'HTTP %{http_code}' http://localhost:3011/login 2>/dev/null || echo 'NOT RESPONDING')"
mark "public_https" "$(curl -sf -o /dev/null -w 'HTTP %{http_code}' https://attendance-lumin.gofaztrack.com/login 2>/dev/null || echo 'NOT RESPONDING')"

# ── 10. CONFLICTS ──
section "10. RESOURCE CONFLICTS"
echo "--- other apps on key ports ---"
ss -tlnp 2>/dev/null | grep -E ":(3011|8011) " | grep -v "next\|uvicorn\|python" || echo "  (none)"

# ── 11. SUMMARY ──
section "11. PREFLIGHT SUMMARY"
TOTAL=$((SECTION_OK + SECTION_PARTIAL + SECTION_BLOCKED))
echo "timestamp: $TS"
echo "script: lumin-prod-preflight.sh (read-only)"
echo "checks_ok: $SECTION_OK"
echo "checks_partial: $SECTION_PARTIAL"
echo "checks_blocked: $SECTION_BLOCKED"
echo "checks_total: $TOTAL"
if [ "$SECTION_BLOCKED" -gt 0 ]; then
  echo "verdict: BLOCKED ($SECTION_BLOCKED checks blocked)"
elif [ "$SECTION_PARTIAL" -gt 0 ]; then
  echo "verdict: PARTIAL ($SECTION_PARTIAL checks partial)"
else
  echo "verdict: PASS (all checks passed)"
fi
echo ""
echo "NOTE: No passwords, tokens, keys, or secrets are displayed."
echo "NOTE: No files, services, or databases are modified."
