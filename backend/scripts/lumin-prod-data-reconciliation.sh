#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-data-reconciliation-v3.sh — READ-ONLY
# Aggregate counts only. No worker codes/PII.
# ─────────────────────────────────────────────────────────
set -euo pipefail

EXPECTED_HOSTNAME="VM-8-230-ubuntu"
EXPECTED_USER="ubuntu"
PG_CONTAINER="attendance-lumin-postgres"
PG_USER="faztrack_lumin"
PG_DB="faztrack_attendance_lumin"

# Validate environment
if [ "$(hostname)" != "$EXPECTED_HOSTNAME" ]; then
  echo "FATAL: Hostname mismatch. Expected $EXPECTED_HOSTNAME, got $(hostname)"
  exit 1
fi
if [ "$(whoami)" != "$EXPECTED_USER" ]; then
  echo "FATAL: Must run as $EXPECTED_USER, got $(whoami)"
  exit 1
fi

run_sql() {
  local result
  result=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c "$1" 2>/dev/null | tr -d ' ')
  if [ -z "$result" ]; then
    echo "QUERY_FAILED"
    return 1
  fi
  echo "$result"
}

echo "=== LUMIN DATA RECONCILIATION ==="
echo "timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo ""

echo "--- WORKERS ---"
echo "total: $(run_sql "SELECT count(*) FROM workers WHERE tenant_id='lumin-park-001'")"
echo "active: $(run_sql "SELECT count(*) FROM workers WHERE tenant_id='lumin-park-001' AND is_active=true")"
echo "inactive: $(run_sql "SELECT count(*) FROM workers WHERE tenant_id='lumin-park-001' AND is_active=false")"
echo ""

echo "--- PER TENANT ---"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c "
SELECT t.code, count(w.id) as total,
  sum(case when w.is_active then 1 else 0 end) as active,
  sum(case when not w.is_active then 1 else 0 end) as inactive
FROM workers w JOIN tenants t ON w.tenant_id = t.id
GROUP BY t.code ORDER BY t.code;
" 2>/dev/null || echo "QUERY_FAILED"
echo ""

echo "--- DUPLICATE CODES ---"
dup=$(run_sql "SELECT count(*) FROM (SELECT code FROM workers WHERE tenant_id='lumin-park-001' GROUP BY code HAVING count(*) > 1) sub")
echo "duplicate_groups: $dup"
echo ""

echo "--- SCHEDULE COVERAGE ---"
echo "total_schedules: $(run_sql "SELECT count(*) FROM employee_schedules WHERE tenant_id='lumin-park-001'")"
echo "active_without_schedule: $(run_sql "SELECT count(*) FROM workers w WHERE w.tenant_id='lumin-park-001' AND w.is_active=true AND w.id NOT IN (SELECT es.worker_id FROM employee_schedules es WHERE es.tenant_id='lumin-park-001')")"
echo ""

echo "--- ATTENDANCE ---"
echo "total_events: $(run_sql "SELECT count(*) FROM attendance_events WHERE tenant_id='lumin-park-001'")"
echo "events_today: $(run_sql "SELECT count(*) FROM attendance_events WHERE tenant_id='lumin-park-001' AND work_date=current_date")"
echo ""

echo "=== RECONCILIATION COMPLETE ==="
echo "No worker codes, names, phone numbers, PINs, or PII displayed."
