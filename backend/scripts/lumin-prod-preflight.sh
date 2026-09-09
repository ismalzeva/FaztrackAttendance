#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-preflight.sh
# READ-ONLY production preflight for Lumin Attendance
# Runs on 43.163.7.128 — does NOT modify anything
# ─────────────────────────────────────────────────────────
set -euo pipefail
export LC_ALL=C

REDACT="<REDACTED>"
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

section() { echo ""; echo "════════════════════════════════════════"; echo "  $1"; echo "════════════════════════════════════════"; }

echo "LUMIN PRODUCTION PREFLIGHT — $TS"
echo "Script: lumin-prod-preflight.sh (read-only)"

# ── 1. SYSTEM ──
section "1. SYSTEM"
echo "hostname: $(hostname)"
echo "os: $(cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '"')"
echo "kernel: $(uname -r)"
echo "disk_root: $(df -h / | tail -1 | awk '{print $2 " total, " $3 " used, " $4 " avail"}')"
echo "ram: $(free -h | awk '/Mem:/{print $2 " total, " $3 " used, " $7 " available"}')"
echo "user: $(whoami)"
echo "uptime: $(uptime -p 2>/dev/null || uptime)"

# ── 2. SERVICES ──
section "2. SERVICES"
echo "--- systemd lumin services ---"
systemctl list-units --type=service --state=running 2>/dev/null | grep -i "lumin\|faztrack" || echo "(none found via systemctl)"
echo ""
echo "--- docker containers ---"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null | grep -i "lumin\|faztrack\|postgres\|caddy" || echo "(none found)"

# ── 3. PORTS ──
section "3. PORTS"
echo "--- listening ports ---"
ss -tlnp 2>/dev/null | grep -E ":(3011|8011|5437|80|443) " || echo "(none found for expected ports)"
echo ""
echo "--- processes on key ports ---"
for port in 3011 8011 5437 80 443; do
  pid=$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP 'pid=\K[0-9]+' | head -1)
  if [ -n "$pid" ]; then
    cmd=$(ps -p "$pid" -o args= 2>/dev/null | head -c 120)
    echo "  port $port → pid=$pid cmd=$cmd"
  else
    echo "  port $port → (not listening)"
  fi
done

# ── 4. REPOSITORY ──
section "4. REPOSITORY"
for dir in /home/ubuntu/lumin-frontend /home/ubuntu/FaztrackAttendance; do
  echo "--- $dir ---"
  if [ -d "$dir/.git" ]; then
    cd "$dir"
    echo "  branch: $(git branch --show-current 2>/dev/null)"
    echo "  sha: $(git rev-parse HEAD 2>/dev/null)"
    echo "  commit: $(git log --oneline -1 2>/dev/null)"
    echo "  status: $(git status --short 2>/dev/null | wc -l) modified files"
    echo "  remote: $(git remote get-url origin 2>/dev/null)"
  else
    echo "  (not a git repository)"
  fi
done

# ── 5. CADDY ──
section "5. CADDY"
echo "--- Caddyfile (attendance-lumin block) ---"
if [ -f /etc/caddy/Caddyfile ]; then
  sed -n '/attendance-lumin.gofaztrack.com/,/^}/p' /etc/caddy/Caddyfile 2>/dev/null || echo "(pattern not found)"
else
  echo "(Caddyfile not found at /etc/caddy/Caddyfile)"
fi
echo ""
echo "--- Caddy version ---"
caddy version 2>/dev/null || echo "(caddy not found)"

# ── 6. DATABASE ──
section "6. DATABASE"
echo "--- PostgreSQL container ---"
pg_container=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i "postgres\|lumin" | head -1)
if [ -n "$pg_container" ]; then
  echo "  container: $pg_container"
  echo "  image: $(docker inspect "$pg_container" --format '{{.Config.Image}}' 2>/dev/null)"
  echo "  tables: $(docker exec "$pg_container" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null | tr -d ' ' || echo 'N/A')"
  echo "  workers: $(docker exec "$pg_container" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM workers WHERE tenant_id='lumin-park-001' AND is_active=true" 2>/dev/null | tr -d ' ' || echo 'N/A')"
  echo "  projects: $(docker exec "$pg_container" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM projects WHERE tenant_id='lumin-park-001'" 2>/dev/null | tr -d ' ' || echo 'N/A')"
  echo "  attendance_events: $(docker exec "$pg_container" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM attendance_events" 2>/dev/null | tr -d ' ' || echo 'N/A')"
  echo "  templates: $(docker exec "$pg_container" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM schedule_templates WHERE tenant_id='lumin-park-001'" 2>/dev/null | tr -d ' ' || echo 'N/A')"
  echo "  holidays: $(docker exec "$pg_container" psql -U faztrack_lumin -d faztrack_attendance_lumin -t -c "SELECT count(*) FROM holiday_calendar WHERE tenant_id='lumin-park-001'" 2>/dev/null | tr -d ' ' || echo 'N/A')"
else
  echo "  (no PostgreSQL container found)"
fi

# ── 7. ENV/CONFIG ──
section "7. CONFIG LOCATIONS"
echo "--- .env files (contents REDACTED) ---"
for f in /home/ubuntu/FaztrackAttendance/backend/.env.lumin /home/ubuntu/lumin-frontend/.env.local; do
  if [ -f "$f" ]; then
    echo "  $f: EXISTS ($(wc -l < "$f") lines)"
  else
    echo "  $f: NOT FOUND"
  fi
done
echo ""
echo "--- Service files ---"
for svc in faztrack-attendance-lumin.service faztrack-attendance-lumin-web.service; do
  f="/etc/systemd/system/$svc"
  if [ -f "$f" ]; then
    echo "  $f: EXISTS"
    grep -E "WorkingDirectory|ExecStart|Environment" "$f" 2>/dev/null | head -5
  else
    echo "  $f: NOT FOUND"
  fi
done

# ── 8. HEALTH ──
section "8. HEALTH CHECKS"
echo "--- backend ---"
curl -sf http://localhost:8011/health/live 2>/dev/null || echo "(backend not responding)"
echo ""
echo "--- frontend ---"
curl -sf -o /dev/null -w "HTTP %{http_code}" http://localhost:3011/login 2>/dev/null || echo "(frontend not responding)"
echo ""
echo "--- public HTTPS ---"
curl -sf -o /dev/null -w "HTTP %{http_code}" https://attendance-lumin.gofaztrack.com/login 2>/dev/null || echo "(public URL not responding)"
echo ""

# ── 9. CONFLICTS ──
section "9. RESOURCE CONFLICTS"
echo "--- other apps on port 3011/8011 ---"
ss -tlnp 2>/dev/null | grep -E ":(3011|8011) " | grep -v "lumin\|faztrack\|next\|uvicorn" || echo "(none)"
echo ""
echo "--- other Postgres on port 5437 ---"
ss -tlnp 2>/dev/null | grep ":5437 " || echo "(none)"

# ── 10. SUMMARY ──
section "10. PREFLIGHT SUMMARY"
echo "timestamp: $TS"
echo "script: lumin-prod-preflight.sh (read-only, no modifications)"
echo "status: COMPLETE"
echo ""
echo "NOTE: This script does NOT display passwords, tokens, keys, or secrets."
echo "NOTE: This script does NOT modify any files, services, or database."
