# Metro Mining (MM) — Technical & Administrator Manual

**Version:** M4E  
**Last Updated:** 2026-08-22  
**Domain:** `attendance-metro.gofaztrack.com`  
**Status:** Production (Pilot)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Deployment & Services](#2-deployment--services)
3. [Configuration Reference](#3-configuration-reference)
4. [Database](#4-database)
5. [API Reference](#5-api-reference)
6. [Frontend](#6-frontend)
7. [Monitoring & Health Checks](#7-monitoring--health-checks)
8. [Backup & Recovery](#8-backup--recovery)
9. [Security](#9-security)
10. [DNS & SSL](#10-dns--ssl)
11. [Known Issues & Gotchas](#11-known-issues--gotchas)
12. [Troubleshooting](#12-troubleshooting)
13. [Operations Runbook](#13-operations-runbook)

---

## 1. Architecture Overview

### 1.1 Multi-Tenant Model

Faztrack Attendance is a **shared-core, multi-tenant** codebase. Metro Mining runs as a **separate application instance** with its own database, systemd services, and subdomain — but shares the same repository and source code as other tenants.

```
┌─────────────────────────────────────────────────────────────┐
│                    Caddy Reverse Proxy                       │
│              (auto-SSL, gzip encoding)                       │
├─────────────────────────────────────────────────────────────┤
│  attendance-metro.gofaztrack.com                            │
│    /api/*  → localhost:8084  (FastAPI backend)               │
│    /*      → localhost:3004  (Next.js frontend)              │
└────────────┬──────────────────────────┬─────────────────────┘
             │                          │
    ┌────────▼────────┐       ┌─────────▼─────────┐
    │  FastAPI Backend │       │  Next.js Frontend  │
    │  (uvicorn:8084)  │       │  (standalone:3004) │
    │  faztrack-       │       │  faztrack-          │
    │  attendance-     │       │  attendance-        │
    │  metro.service   │       │  metro-web.service  │
    └────────┬────────┘       └────────────────────┘
             │
    ┌────────▼────────┐
    │  PostgreSQL 16   │
    │  (Docker)        │
    │  metro-postgres  │
    │  localhost:5436   │
    │  DB: faztrack_   │
    │  attendance_metro│
    └─────────────────┘
```

### 1.2 Technology Stack

| Layer       | Technology              | Version   |
|-------------|-------------------------|-----------|
| Backend     | Python / FastAPI / Uvicorn | 3.11 / 0.115+ |
| Frontend    | Next.js (React 19)      | 15.5.23   |
| Database    | PostgreSQL (Docker)     | 16 Alpine |
| ORM         | SQLAlchemy              | 2.x       |
| Migrations  | Alembic                 | stamped   |
| Reverse Proxy | Caddy               | 2.x       |
| Auth        | JWT (HS256) + PBKDF2-SHA256 |       |
| Process Mgmt | systemd               |           |

### 1.3 Repository Layout

```
/home/ubuntu/FaztrackAttendance/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app entry point
│   │   ├── config.py            # Pydantic Settings (FAZTRACK_ prefix)
│   │   ├── database.py          # SQLAlchemy engine + session
│   │   ├── models.py            # 45 ORM models (single file)
│   │   ├── schemas.py           # Pydantic request/response schemas
│   │   ├── security.py          # JWT + password hashing
│   │   ├── dependencies.py      # Auth deps, tenant context
│   │   ├── audit.py             # Audit trail
│   │   ├── master_data.py       # /api/v1/master-data/* router
│   │   ├── devices.py           # /api/v1/devices/* router
│   │   ├── attendance.py        # /api/v1/attendance/* router
│   │   ├── timesheets.py        # /api/v1/timesheets/* router
│   │   ├── dashboard.py         # /api/v1/dashboard/* router
│   │   ├── roster.py            # /api/v1/roster/* router
│   │   ├── exceptions.py        # /api/v1/exceptions/* router
│   │   ├── reports.py           # /api/v1/reports/* router
│   │   └── ...
│   ├── scripts/
│   │   └── seed_metro_standalone.py  # Idempotent seed script
│   ├── .env.metro               # Metro-specific environment
│   └── alembic.ini              # Alembic config (SQLite default)
├── frontend/
│   ├── app/                     # Next.js App Router pages
│   │   ├── page.tsx             # Landing / redirect
│   │   ├── login/               # Login page
│   │   ├── attendance/          # Attendance management
│   │   ├── devices/             # Device management
│   │   ├── enroll/              # Worker enrollment
│   │   ├── review/              # Exception review
│   │   ├── timesheets/          # Timesheet management
│   │   └── layout.tsx           # Root layout
│   ├── next.config.ts           # output: "standalone"
│   └── package.json
└── docs/                        # This manual + acceptance reports
```

---

## 2. Deployment & Services

### 2.1 Systemd Services

**Backend service:**

```bash
# Service file
/etc/systemd/system/faztrack-attendance-metro.service

# Manage
sudo systemctl start faztrack-attendance-metro
sudo systemctl stop faztrack-attendance-metro
sudo systemctl restart faztrack-attendance-metro
sudo systemctl status faztrack-attendance-metro
```

**Frontend service:**

```bash
# Service file
/etc/systemd/system/faztrack-attendance-metro-web.service

# Manage
sudo systemctl start faztrack-attendance-metro-web
sudo systemctl stop faztrack-attendance-metro-web
sudo systemctl restart faztrack-attendance-metro-web
sudo systemctl status faztrack-attendance-metro-web
```

### 2.2 Database Container

```bash
# Container: metro-postgres
# Image: postgres:16-alpine
# Port mapping: host 5436 → container 5432

# Check status
docker ps --filter name=metro-postgres

# Start/stop
docker start metro-postgres
docker stop metro-postgres

# Logs
docker logs metro-postgres --tail 50 -f
```

### 2.3 Starting the Full Stack

```bash
# 1. Ensure database is running
docker start metro-postgres

# 2. Start backend
sudo systemctl start faztrack-attendance-metro

# 3. Start frontend
sudo systemctl start faztrack-attendance-metro-web

# 4. Verify all services
docker ps --filter name=metro-postgres
systemctl is-active faztrack-attendance-metro
systemctl is-active faztrack-attendance-metro-web
```

### 2.4 Ports

| Service    | Port | Binding    | Protocol |
|------------|------|------------|----------|
| Backend    | 8084 | 127.0.0.1  | HTTP     |
| Frontend   | 3004 | 127.0.0.1  | HTTP     |
| PostgreSQL | 5436 | 0.0.0.0    | TCP      |
| Caddy      | 443  | 0.0.0.0    | HTTPS    |
| Caddy      | 80   | 0.0.0.0    | HTTP→301 |

---

## 3. Configuration Reference

### 3.1 Environment File

**Path:** `/home/ubuntu/FaztrackAttendance/backend/.env.metro`

```bash
# Environment
FAZTRACK_ENV=production

# Database connection (psycopg3 driver)
FAZTRACK_DATABASE_URL=postgresql+psycopg://faztrack_metro:<PASSWORD>@localhost:5436/faztrack_attendance_metro

# CORS — restrict to Metro domain + local dev
FAZTRACK_CORS_ORIGINS=["https://attendance-metro.gofaztrack.com","http://localhost:3004"]

# Demo worker PIN (for pilot testing)
FAZTRACK_DEMO_WORKER_PIN=123456

# JWT secret (auto-generated, do not share)
FAZTRACK_JWT_SECRET=<secret>

# Token expiry (minutes)
FAZTRACK_ACCESS_TOKEN_MINUTES=30
```

> **Security Note:** The `.env.metro` file contains secrets. Restrict access:
> ```bash
> chmod 600 /home/ubuntu/FaztrackAttendance/backend/.env.metro
> ```

### 3.2 Settings Class

All `FAZTRACK_*` environment variables map to the `Settings` class in `backend/app/config.py`:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FAZTRACK_", extra="ignore")
    env: str = "development"
    database_url: str = "sqlite:///./faztrack_attendance.db"
    jwt_secret: str = "development-only-secret-change-before-production"
    access_token_minutes: int = 30
    cors_origins: list[str] = ["http://localhost:3000"]
    demo_seed_password: str | None = None
    demo_worker_pin: str | None = None
```

### 3.3 Caddy Configuration

**Path:** `/etc/caddy/Caddyfile`

```caddyfile
attendance-metro.gofaztrack.com {
    reverse_proxy /api/* localhost:8084
    reverse_proxy localhost:3004
    encode gzip
}
```

**Reload after changes:**

```bash
sudo systemctl reload caddy
```

### 3.4 Frontend Configuration

**Path:** `/home/ubuntu/FaztrackAttendance/frontend/next.config.ts`

```typescript
import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone"
};
export default nextConfig;
```

The frontend connects to the backend via relative `/api/*` paths — Caddy handles routing. No `NEXT_PUBLIC_API_URL` is needed.

---

## 4. Database

### 4.1 Connection Details

| Parameter   | Value                                      |
|-------------|--------------------------------------------|
| Host        | localhost                                  |
| Port        | 5436                                       |
| Database    | faztrack_attendance_metro                  |
| User        | faztrack_metro                             |
| Password    | (see `.env.metro` → `FAZTRACK_DATABASE_URL`) |
| Driver      | psycopg (psycopg3)                         |
| SQLAlchemy  | `postgresql+psycopg://`                    |

### 4.2 Schema

The database contains **45 tables** created via SQLAlchemy `Base.metadata.create_all()`. Key tables:

| Category        | Tables                                                        |
|-----------------|---------------------------------------------------------------|
| **Tenancy**     | `tenants`, `users`, `memberships`, `project_scopes`           |
| **Projects**    | `projects`, `workers`, `assignments`, `work_schedules`        |
| **Attendance**  | `attendance_events`, `attendance_policies`, `canonical_attendance_events` |
| **Devices**     | `device_challenges`, `device_enrollments`, `device_bindings`  |
| **Timesheets**  | `timesheet_periods`                                           |
| **Roster**      | `roster_assignments`, `roster_policies`, `shift_templates`, `crews` |
| **Equipment**   | `equipment`, `equipment_assignment_actuals`, `equipment_comparison_results`, `equipment_discrepancies` |
| **Exceptions**  | `exception_events`, `override_events`                         |
| **Checkpoints** | `checkpoint_policies`, `checkpoint_event_mappings`            |
| **Audit**       | `audit_events`                                                |
| **Rules**       | `rule_versions`, `operational_rule_engine`                    |
| **Import**      | `import_batches`, `raw_events`                                |
| **Other**       | `sites`, `roles`, `competencies`, `employee_meta`, `attendance_challenges` |

### 4.3 Alembic Migrations

Alembic is configured but Metro was initialized via `create_all()` + stamped to head:

```bash
# Alembic config
/home/ubuntu/FaztrackAttendance/backend/alembic.ini

# Current revision (stamped)
# Migration: 012_m4b_roster_operational
```

**To check current revision:**

```bash
cd /home/ubuntu/FaztrackAttendance/backend
source .venv/bin/activate
FAZTRACK_DATABASE_URL="postgresql+psycopg://faztrack_metro:<PASSWORD>@localhost:5436/faztrack_attendance_metro" \
  alembic current
```

**To apply future migrations:**

```bash
cd /home/ubuntu/FaztrackAttendance/backend
source .venv/bin/activate
FAZTRACK_DATABASE_URL="postgresql+psycopg://faztrack_metro:<PASSWORD>@localhost:5436/faztrack_attendance_metro" \
  alembic upgrade head
```

### 4.4 Seed Script

**Path:** `/home/ubuntu/FaztrackAttendance/backend/scripts/seed_metro_standalone.py`

The seed script is **idempotent** — safe to re-run. It creates:
- Tenant: Metro Mining (`metro-mining`)
- Admin user + memberships
- Projects, sites, workers, assignments
- Shift templates, crews, competencies
- Equipment, checkpoint policies
- Demo attendance events and timesheets

**Run the seed:**

```bash
cd /home/ubuntu/FaztrackAttendance/backend
source .venv/bin/activate
FAZTRACK_DATABASE_URL="postgresql+psycopg://faztrack_metro:<PASSWORD>@localhost:5436/faztrack_attendance_metro" \
  python3 scripts/seed_metro_standalone.py
```

**Re-seed (reset data):**

The script uses `merge()` and `get_or_create()` patterns, so re-running updates existing records without duplicates. For a full reset:

```bash
# 1. Drop and recreate database
docker exec -it metro-postgres psql -U faztrack_metro -c "DROP DATABASE faztrack_attendance_metro;"
docker exec -it metro-postgres psql -U faztrack_metro -c "CREATE DATABASE faztrack_attendance_metro;"

# 2. Restart backend (triggers create_all)
sudo systemctl restart faztrack-attendance-metro

# 3. Re-seed
cd /home/ubuntu/FaztrackAttendance/backend
source .venv/bin/activate
FAZTRACK_DATABASE_URL="postgresql+psycopg://faztrack_metro:<PASSWORD>@localhost:5436/faztrack_attendance_metro" \
  python3 scripts/seed_metro_standalone.py
```

### 4.5 Direct Database Access

```bash
# Via docker exec
docker exec -it metro-postgres psql -U faztrack_metro -d faztrack_attendance_metro

# Via psql on host (if installed)
psql -h localhost -p 5436 -U faztrack_metro -d faztrack_attendance_metro

# Common queries
SELECT count(*) FROM tenants;
SELECT login_id, is_active FROM users;
SELECT name, status FROM projects;
```

---

## 5. API Reference

### 5.1 Base URL

```
https://attendance-metro.gofaztrack.com/api/v1
```

### 5.2 Authentication

All API calls (except `/health/*` and `/api/v1/auth/login`) require:

1. **Bearer token** in `Authorization` header
2. **X-Tenant-ID** header with the tenant UUID

```bash
# Login
curl -X POST https://attendance-metro.gofaztrack.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"login_id": "admin@metro", "password": "<password>"}'

# Response
{
  "data": {
    "access_token": "eyJ...",
    "token_type": "bearer"
  },
  "meta": {
    "correlation_id": "...",
    "server_time": "...",
    "version": "v1"
  }
}

# Authenticated request
curl https://attendance-metro.gofaztrack.com/api/v1/master-data/projects \
  -H "Authorization: Bearer eyJ..." \
  -H "X-Tenant-ID: <tenant-uuid>"
```

### 5.3 API Routers

| Router           | Prefix                          | Description                    |
|------------------|---------------------------------|--------------------------------|
| `master_data`    | `/api/v1/master-data/*`         | Projects, workers, sites, etc. |
| `devices`        | `/api/v1/devices/*`             | Device enrollment & binding    |
| `attendance`     | `/api/v1/attendance/*`          | Clock-in/out, events           |
| `timesheets`     | `/api/v1/timesheets/*`          | Period management              |
| `dashboard`      | `/api/v1/dashboard/*`           | Aggregated views               |
| `roster`         | `/api/v1/roster/*`              | Shift & crew management        |
| `exceptions`     | `/api/v1/exceptions/*`          | Exception review workflow       |
| `reports`        | `/api/v1/reports/*`             | Reporting endpoints            |

### 5.4 Response Envelope

All API responses follow this envelope:

```json
{
  "data": { ... },
  "meta": {
    "correlation_id": "uuid",
    "server_time": "ISO-8601",
    "version": "v1"
  }
}
```

### 5.5 Correlation IDs

Every request gets an `X-Correlation-ID` (from request header or auto-generated UUID). It's returned in the response header and in the `meta` body for tracing.

### 5.6 Health Endpoints

```bash
# Liveness (no DB check)
curl https://attendance-metro.gofaztrack.com/health/live
# → {"status": "ok"}

# Readiness (DB connectivity check)
curl https://attendance-metro.gofaztrack.com/health/ready
# → {"status": "ready"}
```

### 5.7 API Documentation

Swagger UI is **disabled in production** (`env=production`). To enable temporarily:

```bash
# Edit main.py: change docs_url condition
# Or set FAZTRACK_ENV=development in .env.metro (NOT recommended for prod)
```

---

## 6. Frontend

### 6.1 Pages

| Route          | Description                          |
|----------------|--------------------------------------|
| `/`            | Landing page / redirect to login     |
| `/login`       | Authentication                       |
| `/attendance`  | Attendance management                |
| `/devices`     | Device management                    |
| `/enroll`      | Worker enrollment                    |
| `/review`      | Exception review                     |
| `/timesheets`  | Timesheet management                 |

### 6.2 Build & Deploy

```bash
cd /home/ubuntu/FaztrackAttendance/frontend

# Install dependencies
npm ci

# Build standalone
npm run build

# The standalone output is at:
# /home/ubuntu/FaztrackAttendance/frontend/.next/standalone/

# Start (managed by systemd)
node .next/standalone/server.js -p 3004
```

### 6.3 Frontend-Backend Communication

The frontend uses **relative API paths** (`/api/v1/*`). Caddy routes:
- `/api/*` → backend on port 8084
- `/*` → frontend on port 3004

No CORS issues in production since both are served from the same domain.

---

## 7. Monitoring & Health Checks

### 7.1 Health Endpoints

```bash
# Liveness — always returns 200 if process is up
curl -s https://attendance-metro.gofaztrack.com/health/live | jq .

# Readiness — returns 200 only if DB is reachable
curl -s https://attendance-metro.gofaztrack.com/health/ready | jq .
```

### 7.2 Logs

```bash
# Backend logs (real-time)
journalctl -u faztrack-attendance-metro -f

# Backend logs (last 100 lines)
journalctl -u faztrack-attendance-metro -n 100 --no-pager

# Frontend logs (real-time)
journalctl -u faztrack-attendance-metro-web -f

# Frontend logs (last 100 lines)
journalctl -u faztrack-attendance-metro-web -n 100 --no-pager

# Database logs
docker logs metro-postgres --tail 50 -f

# Caddy logs
journalctl -u caddy -f
```

### 7.3 Service Status

```bash
# Quick health check script
echo "=== Database ==="
docker ps --filter name=metro-postgres --format "{{.Names}}: {{.Status}}"

echo "=== Backend ==="
systemctl is-active faztrack-attendance-metro

echo "=== Frontend ==="
systemctl is-active faztrack-attendance-metro-web

echo "=== API Health ==="
curl -s http://localhost:8084/health/live

echo "=== DB Readiness ==="
curl -s http://localhost:8084/health/ready
```

### 7.4 Audit Trail

All mutations are recorded in the `audit_events` table:

```sql
SELECT created_at, action, entity_type, entity_id, actor_user_id
FROM audit_events
ORDER BY created_at DESC
LIMIT 20;
```

---

## 8. Backup & Recovery

### 8.1 Database Backup

```bash
# Full backup (plain SQL)
docker exec metro-postgres pg_dump -U faztrack_metro faztrack_attendance_metro > \
  /home/ubuntu/backups/metro_$(date +%Y%m%d_%H%M%S).sql

# Compressed backup
docker exec metro-postgres pg_dump -U faztrack_metro -Fc faztrack_attendance_metro > \
  /home/ubuntu/backups/metro_$(date +%Y%m%d_%H%M%S).dump

# Schema-only backup
docker exec metro-postgres pg_dump -U faztrack_metro --schema-only faztrack_attendance_metro > \
  /home/ubuntu/backups/metro_schema_$(date +%Y%m%d_%H%M%S).sql
```

### 8.2 Database Restore

```bash
# From plain SQL
docker exec -i metro-postgres psql -U faztrack_metro -d faztrack_attendance_metro < \
  /home/ubuntu/backups/metro_20260822_120000.sql

# From custom format
docker exec -i metro-postgres pg_restore -U faztrack_metro -d faztrack_attendance_metro \
  --clean --if-exists < /home/ubuntu/backups/metro_20260822_120000.dump
```

### 8.3 Environment Backup

```bash
# Backup environment file
cp /home/ubuntu/FaztrackAttendance/backend/.env.metro \
   /home/ubuntu/backups/env.metro.$(date +%Y%m%d_%H%M%S)

# Backup systemd service files
cp /etc/systemd/system/faztrack-attendance-metro.service \
   /home/ubuntu/backups/
cp /etc/systemd/system/faztrack-attendance-metro-web.service \
   /home/ubuntu/backups/

# Backup Caddy config
cp /etc/caddy/Caddyfile /home/ubuntu/backups/caddyfile.$(date +%Y%m%d_%H%M%S)
```

### 8.4 Automated Backup (Recommended)

```bash
# Add to crontab: daily at 2 AM
0 2 * * * docker exec metro-postgres pg_dump -U faztrack_metro -Fc faztrack_attendance_metro > /home/ubuntu/backups/metro_$(date +\%Y\%m\%d).dump 2>> /home/ubuntu/backups/backup.log
```

---

## 9. Security

### 9.1 Authentication

- **JWT (HS256)** tokens with configurable expiry (default: 30 minutes)
- Token payload: `{"sub": user_id, "iat": ..., "exp": ..., "type": "access"}`
- Worker tokens: `{"sub": worker_id, "tenant_id": ..., "type": "worker"}`

### 9.2 Password Hashing

- **Algorithm:** PBKDF2-SHA256
- **Iterations:** 210,000
- **Salt:** 16 bytes (random per password)
- **Format:** `pbkdf2_sha256$210000$<base64-salt>$<base64-digest>`

### 9.3 Tenant Isolation

Every authenticated API call requires the `X-Tenant-ID` header. The `tenant_context` dependency:

1. Extracts the user from the JWT token
2. Validates the user has an **ACTIVE** membership in the specified tenant
3. Loads the user's project scopes for that tenant
4. All subsequent queries are scoped to the tenant

```python
def tenant_context(
    x_tenant_id: str = Header(alias="X-Tenant-ID"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db)
) -> RequestContext:
    membership = db.scalar(select(Membership).where(
        Membership.user_id == user.id,
        Membership.tenant_id == x_tenant_id,
        Membership.status == MembershipStatus.ACTIVE
    ))
    # ...
```

### 9.4 CORS Policy

Restricted to:
- `https://attendance-metro.gofaztrack.com` (production)
- `http://localhost:3004` (local development)

### 9.5 Network Security

- Backend and frontend bind to `127.0.0.1` (not exposed externally)
- Only Caddy (ports 80/443) is publicly accessible
- PostgreSQL port 5436 is exposed on `0.0.0.0` — **restrict via firewall if not needed externally**

```bash
# Recommended: restrict PostgreSQL to localhost only
sudo ufw deny 5436
# Or bind to 127.0.0.1 in docker-compose
```

### 9.6 Secrets Management

| Secret           | Location                          | Rotate Frequency |
|------------------|-----------------------------------|------------------|
| JWT Secret       | `.env.metro` → `FAZTRACK_JWT_SECRET` | Quarterly     |
| DB Password      | `.env.metro` → `FAZTRACK_DATABASE_URL` | Quarterly   |
| Demo Worker PIN  | `.env.metro` → `FAZTRACK_DEMO_WORKER_PIN` | Per pilot phase |

---

## 10. DNS & SSL

### 10.1 DNS Setup

**Required DNS record:**

```
attendance-metro.gofaztrack.com.  A  43.134.112.7
```

### 10.2 SSL/TLS

Caddy **automatically provisions** Let's Encrypt SSL certificates after DNS resolves:

```bash
# Verify SSL
curl -vI https://attendance-metro.gofaztrack.com 2>&1 | grep -i "SSL\|certificate"

# Check certificate expiry
echo | openssl s_client -connect attendance-metro.gofaztrack.com:443 2>/dev/null | openssl x509 -noout -dates
```

### 10.3 Caddy Reload

After modifying the Caddyfile:

```bash
# Validate config
caddy validate --config /etc/caddy/Caddyfile

# Reload (zero-downtime)
sudo systemctl reload caddy

# Or restart
sudo systemctl restart caddy
```

---

## 11. Known Issues & Gotchas

### 11.1 `group_concat` SQLite vs PostgreSQL

The codebase was originally developed with SQLite, which uses `group_concat()`. PostgreSQL uses `string_agg()`. If you encounter:

```
ERROR: function group_concat(...) does not exist
```

**Fix:** Use dialect-aware aggregation:

```python
from sqlalchemy import case, func, literal_column
from sqlalchemy.dialects.postgresql import aggregate_order_by

# PostgreSQL
func.string_agg(column, ',').filter(condition)

# SQLite
func.group_concat(column, ',')
```

### 11.2 Alembic Duplicate Enum Definitions

Alembic migrations contain duplicate `CREATE TYPE ... AS ENUM` statements that fail on PostgreSQL. The Metro instance was initialized using `Base.metadata.create_all()` instead of running migrations, then stamped to head.

**Do not run `alembic upgrade head` from scratch** on a fresh database — use `create_all()` + `alembic stamp head`.

### 11.3 M4A–M4D Features Are API-Only

Features from milestones M4A through M4D (equipment engine, checkpoint engine, operational rule engine, decision engine) are implemented as **backend API endpoints only**. There are no corresponding frontend pages. These features are accessible via:

```bash
curl -H "Authorization: Bearer <token>" \
     -H "X-Tenant-ID: <tenant-id>" \
     https://attendance-metro.gofaztrack.com/api/v1/...
```

### 11.4 Frontend Pages vs Backend Features

| Feature Area       | Backend API | Frontend Page |
|--------------------|:-----------:|:-------------:|
| Auth / Login       | ✅          | ✅            |
| Master Data        | ✅          | ✅            |
| Devices            | ✅          | ✅            |
| Attendance         | ✅          | ✅            |
| Timesheets         | ✅          | ✅            |
| Dashboard          | ✅          | ✅            |
| Roster             | ✅          | ❌            |
| Exceptions         | ✅          | ✅            |
| Reports            | ✅          | ❌            |
| Equipment Engine   | ✅          | ❌            |
| Checkpoint Engine  | ✅          | ❌            |
| Rule Engine        | ✅          | ❌            |
| Decision Engine    | ✅          | ❌            |

---

## 12. Troubleshooting

### 12.1 Service Won't Start

```bash
# Check service status and recent logs
systemctl status faztrack-attendance-metro
journalctl -u faztrack-attendance-metro -n 50 --no-pager

# Common causes:
# - Port 8084 already in use
sudo lsof -i :8084

# - Missing .env.metro
ls -la /home/ubuntu/FaztrackAttendance/backend/.env.metro

# - Python venv missing
ls /home/ubuntu/FaztrackAttendance/backend/.venv/bin/python
```

### 12.2 Database Connection Refused

```bash
# Check if container is running
docker ps --filter name=metro-postgres

# If not running, start it
docker start metro-postgres

# Check container logs
docker logs metro-postgres --tail 20

# Test connection
docker exec metro-postgres pg_isready -U faztrack_metro -d faztrack_attendance_metro

# Test from host
psql -h localhost -p 5436 -U faztrack_metro -d faztrack_attendance_metro -c "SELECT 1;"
```

### 12.3 CORS Errors

```bash
# Check CORS_ORIGINS in .env.metro
grep CORS /home/ubuntu/FaztrackAttendance/backend/.env.metro

# Ensure the domain matches exactly (https, no trailing slash)
# Correct:   https://attendance-metro.gofaztrack.com
# Wrong:     https://attendance-metro.gofaztrack.com/
# Wrong:     http://attendance-metro.gofaztrack.com

# After fixing, restart backend
sudo systemctl restart faztrack-attendance-metro
```

### 12.4 502 Bad Gateway from Caddy

```bash
# Backend not running
systemctl is-active faztrack-attendance-metro

# Frontend not running
systemctl is-active faztrack-attendance-metro-web

# Check if ports are listening
ss -tlnp | grep -E "8084|3004"
```

### 12.5 JWT Token Errors

```bash
# "SESSION_EXPIRED" — token expired or invalid
# Re-login to get a fresh token

# "INVALID_CREDENTIALS" — wrong login_id or password
# Check user exists:
docker exec metro-postgres psql -U faztrack_metro -d faztrack_attendance_metro \
  -c "SELECT login_id, is_active FROM users;"

# JWT_SECRET mismatch (e.g., after env file change)
# Restart backend to reload settings
sudo systemctl restart faztrack-attendance-metro
```

### 12.6 Frontend Not Loading

```bash
# Check frontend service
systemctl status faztrack-attendance-metro-web
journalctl -u faztrack-attendance-metro-web -n 30

# Check if standalone build exists
ls /home/ubuntu/FaztrackAttendance/frontend/.next/standalone/server.js

# Rebuild if needed
cd /home/ubuntu/FaztrackAttendance/frontend
npm ci && npm run build
sudo systemctl restart faztrack-attendance-metro-web
```

### 12.7 Seed Script Fails

```bash
# Ensure DATABASE_URL is set correctly
cd /home/ubuntu/FaztrackAttendance/backend
source .venv/bin/activate
FAZTRACK_DATABASE_URL="postgresql+psycopg://faztrack_metro:<PASSWORD>@localhost:5436/faztrack_attendance_metro" \
  python3 scripts/seed_metro_standalone.py

# If "relation does not exist" — tables not created
# Restart backend to trigger create_all(), then re-run seed
sudo systemctl restart faztrack-attendance-metro
```

---

## 13. Operations Runbook

### 13.1 Full Restart Sequence

```bash
# 1. Restart database
docker restart metro-postgres
sleep 5

# 2. Restart backend
sudo systemctl restart faztrack-attendance-metro
sleep 3

# 3. Restart frontend
sudo systemctl restart faztrack-attendance-metro-web

# 4. Verify
curl -s http://localhost:8084/health/ready | jq .
curl -s http://localhost:3004/ -o /dev/null -w "%{http_code}"
```

### 13.2 Update Deployment

```bash
# 1. Pull latest code
cd /home/ubuntu/FaztrackAttendance
git pull origin main

# 2. Update backend dependencies (if changed)
cd backend
source .venv/bin/activate
pip install -r requirements.txt

# 3. Run any new migrations (if applicable)
# FAZTRACK_DATABASE_URL="..." alembic upgrade head

# 4. Restart backend
sudo systemctl restart faztrack-attendance-metro

# 5. Rebuild frontend (if changed)
cd ../frontend
npm ci
npm run build

# 6. Restart frontend
sudo systemctl restart faztrack-attendance-metro-web

# 7. Verify
curl -s http://localhost:8084/health/ready | jq .
```

### 13.3 Database Maintenance

```bash
# Vacuum and analyze
docker exec metro-postgres psql -U faztrack_metro -d faztrack_attendance_metro \
  -c "VACUUM ANALYZE;"

# Check table sizes
docker exec metro-postgres psql -U faztrack_metro -d faztrack_attendance_metro -c "
  SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
  FROM pg_catalog.pg_statio_user_tables
  ORDER BY pg_total_relation_size(relid) DESC
  LIMIT 10;
"

# Check connection count
docker exec metro-postgres psql -U faztrack_metro -d faztrack_attendance_metro -c "
  SELECT count(*) FROM pg_stat_activity WHERE datname = 'faztrack_attendance_metro';
"
```

### 13.4 Log Rotation

Systemd journal is managed by `journald`. To limit disk usage:

```bash
# Check current journal size
journalctl --disk-usage

# Limit to 500MB
sudo journalctl --vacuum-size=500M

# Or set permanently in /etc/systemd/journald.conf:
# SystemMaxUse=500M
```

### 13.5 Firewall Rules

```bash
# Allow only necessary ports
sudo ufw allow 80/tcp    # HTTP (Caddy redirect)
sudo ufw allow 443/tcp   # HTTPS (Caddy)
sudo ufw deny 8084/tcp   # Block direct backend access
sudo ufw deny 3004/tcp   # Block direct frontend access
sudo ufw deny 5436/tcp   # Block direct DB access (optional)
```

---

## Appendix A: Quick Reference Card

```
┌──────────────────────────────────────────────────────────────┐
│                    METRO MINING QUICK REF                     │
├──────────────────────────────────────────────────────────────┤
│ Domain:    attendance-metro.gofaztrack.com                    │
│ Backend:   localhost:8084 (faztrack-attendance-metro.service) │
│ Frontend:  localhost:3004 (faztrack-attendance-metro-web)     │
│ Database:  localhost:5436 (metro-postgres Docker)             │
│ DB Name:   faztrack_attendance_metro                          │
│ DB User:   faztrack_metro                                     │
│ Env File:  backend/.env.metro                                 │
│ Seed:      backend/scripts/seed_metro_standalone.py           │
├──────────────────────────────────────────────────────────────┤
│ HEALTH:                                                      │
│   curl localhost:8084/health/live                             │
│   curl localhost:8084/health/ready                            │
├──────────────────────────────────────────────────────────────┤
│ LOGS:                                                        │
│   journalctl -u faztrack-attendance-metro -f                  │
│   journalctl -u faztrack-attendance-metro-web -f              │
│   docker logs metro-postgres -f                               │
├──────────────────────────────────────────────────────────────┤
│ RESTART:                                                     │
│   docker restart metro-postgres                               │
│   sudo systemctl restart faztrack-attendance-metro            │
│   sudo systemctl restart faztrack-attendance-metro-web        │
├──────────────────────────────────────────────────────────────┤
│ BACKUP:                                                      │
│   docker exec metro-postgres pg_dump -U faztrack_metro \      │
│     faztrack_attendance_metro > backup.sql                    │
└──────────────────────────────────────────────────────────────┘
```

---

*This document is maintained as part of the Faztrack Attendance project. For questions or updates, contact the Faztrack engineering team.*
