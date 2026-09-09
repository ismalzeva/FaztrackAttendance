#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-prod-data-reconciliation.sh
# READ-ONLY data reconciliation for production
# Explains: workers total/active/inactive, schedules, discrepancies
# ─────────────────────────────────────────────────────────
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-lumin-postgres}"
PG_USER="faztrack_lumin"
PG_DB="faztrack_attendance_lumin"

run_sql() {
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c "$1" 2>/dev/null | tr -d ' ' || echo "N/A"
}

echo "=== LUMIN PRODUCTION DATA RECONCILIATION ==="
echo "timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo ""

# Total workers
echo "--- WORKERS ---"
echo "total_workers: $(run_sql "SELECT count(*) FROM workers WHERE tenant_id='lumin-park-001'")"
echo "active_workers: $(run_sql "SELECT count(*) FROM workers WHERE tenant_id='lumin-park-001' AND is_active=true")"
echo "inactive_workers: $(run_sql "SELECT count(*) FROM workers WHERE tenant_id='lumin-park-001' AND is_active=false")"
echo ""

# Per tenant
echo "--- PER TENANT ---"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c "
SELECT t.code, count(w.id) as total, 
  sum(case when w.is_active then 1 else 0 end) as active,
  sum(case when not w.is_active then 1 else 0 end) as inactive
FROM workers w JOIN tenants t ON w.tenant_id = t.id
GROUP BY t.code ORDER BY t.code;
" 2>/dev/null || echo "(query failed)"
echo ""

# Duplicate worker codes
echo "--- DUPLICATE WORKER CODES ---"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c "
SELECT code, count(*) as cnt FROM workers 
WHERE tenant_id='lumin-park-001' GROUP BY code HAVING count(*) > 1;
" 2>/dev/null || echo "(query failed)"
dup_result=$(run_sql "SELECT count(*) FROM (SELECT code FROM workers WHERE tenant_id='lumin-park-001' GROUP BY code HAVING count(*) > 1) sub")
echo "duplicate_codes: $dup_result"
echo ""

# Active workers without schedule
echo "--- ACTIVE WITHOUT SCHEDULE ---"
echo "count: $(run_sql "SELECT count(*) FROM workers w WHERE w.tenant_id='lumin-park-001' AND w.is_active=true AND w.id NOT IN (SELECT es.worker_id FROM employee_schedules es WHERE es.tenant_id='lumin-park-001')")"
echo ""

# Worker codes (no names/PII)
echo "--- WORKER CODES (tenant=lumin-park-001) ---"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c "
SELECT code, is_active FROM workers WHERE tenant_id='lumin-park-001' ORDER BY code;
" 2>/dev/null || echo "(query failed)"
echo ""

# Schedules
echo "--- SCHEDULES ---"
echo "total_schedules: $(run_sql "SELECT count(*) FROM employee_schedules WHERE tenant_id='lumin-park-001'")"
echo "schedule_templates: $(run_sql "SELECT count(*) FROM schedule_templates WHERE tenant_id='lumin-park-001'")"
echo ""

# Attendance events
echo "--- ATTENDANCE ---"
echo "total_events: $(run_sql "SELECT count(*) FROM attendance_events WHERE tenant_id='lumin-park-001'")"
echo "events_today: $(run_sql "SELECT count(*) FROM attendance_events WHERE tenant_id='lumin-park-001' AND work_date=current_date")"
echo ""

echo "=== RECONCILIATION COMPLETE ==="
echo "NOTE: No names, phone numbers, PINs, or PII displayed."
echo "NOTE: READ-ONLY — no data modified."
