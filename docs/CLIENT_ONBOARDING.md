# Faztrack Attendance — Client Onboarding Guide

## Gambaran

`fzctl` adalah CLI tool untuk mengelola lifecycle tenant klien Faztrack Attendance:
onboarding → backup → restore → info → list.

Satu tenant = satu instance terisolasi (DB terpisah + service systemd + frontend tree).

**Lokasi**: `scripts/fzctl.py`  
**Registry**: `scripts/tenants.json` (metadata saja, NO secret)  
**Template**: `scripts/templates/` (env, systemd, seed stub)

---

## Prasyarat Satu Kali (Sudah Ada)

- Python venv di backend (shared: `/home/ubuntu/FaztrackAttendance/backend/.venv`)
- Docker (untuk PostgreSQL container)
- systemd (untuk service unit)
- Node.js + npm (untuk Next.js frontend build)

---

## Quickstart

```bash
cd /home/ubuntu/FaztrackAttendance

# 1. Lihat tenant eksisting
python3 scripts/fzctl.py list

# 2. Info detail satu tenant
python3 scripts/fzctl.py info --slug metro

# 3. Onboarding klien baru
python3 scripts/fzctl.py onboard \
  --slug acme \
  --name "Acme Corp" \
  --tier enterprise \
  --timezone Asia/Jakarta \
  --vertical mining

# 4. Backup
python3 scripts/fzctl.py backup --slug acme

# 5. Restore
python3 scripts/fzctl.py restore --slug acme --file backups/acme/dump.sql.gz
```

---

## Tier Isolasi

| Tier | DB | Container | Cocok untuk |
|---|---|---|---|
| **enterprise** | 1 dedicated PostgreSQL container | `<slug>-postgres` | Klien besar, SLA kontrak, isolasi penuh |
| **smb** | 1 database dalam shared container | `faztrack-smb-postgres` | UMKM, multi-tenant ringan, efisiensi resource |

Kedua tier diisolasi penuh secara data (tidak campur). Enterprise hanya lebih mahal resource.

---

## Onboarding Detail

```bash
python3 scripts/fzctl.py onboard \
  --slug <slug> \            # wajib: short id (acme, metro, lumin)
  --name "Nama Perusahaan" \ # wajib: display name
  --tier enterprise|smb \    # default: enterprise
  --timezone Asia/Jakarta \  # default: Asia/Jakarta
  --vertical mining|property|retail|... \  # opsional
  --admin-login admin.xxx \  # default: admin.<slug>
  --no-seed \                # skip admin seed
  --skip-frontend \          # skip npm build (heavy)
  --dry-run \                # lihat rencana tanpa eksekusi
  --db-port 9900 \           # override manual
  --backend-port 9901 \      # override manual
  --frontend-port 9902 \     # override manual
  --domain app.acme.com      # catat untuk Caddy/DNS (opsional)
```

### Langkah yang dijalankan `onboard`:

1. **DB provisioning** — create PostgreSQL container (enterprise) atau database dalam shared container (smb)
2. **Env file** — tulis `backend/.env.<slug>` (mode 0600) dengan secret auto-generated
3. **Migrasi schema** — `alembic upgrade head` terhadap DB baru
4. **Systemd units** — render 2 unit (backend + frontend) → install ke `/etc/systemd/system`
5. **Minimal seed** — `seed_<slug>_standalone.py` (tenant + admin user), idempotent, skip dengan `--no-seed`
6. **Start backend** — `systemctl start`
7. **Frontend build** — rsync `frontend/` → `~/.<slug>-frontend/`, `npm ci`, `npm run build`, static copy, start service. Skip dengan `--skip-frontend`
8. **Register** — catat metadata di `tenants.json`

---

## Build Frontend (Terpisah)

Frontend Next.js di-build per-tenant secara terpisah (tidak sharing `.next/standalone`)
karena `NEXT_PUBLIC_API_BASE_URL` di-bake saat build time.

```bash
# Build ulang frontend (mis. setelah ganti API domain)
python3 scripts/fzctl.py build-frontend --slug acme --api-domain api.acme.com

# Build tanpa start service
python3 scripts/fzctl.py build-frontend --slug acme --no-start
```

---

## Backup & Restore

```bash
# Backup: dump DB + copy .env file
python3 scripts/fzctl.py backup --slug acme
# Output di: backups/acme/

# Restore: DROP SCHEMA, restore dump
python3 scripts/fzctl.py restore --slug acme --file backups/acme/faztrack_attendance_acme-20260827_120000.sql.gz
```

---

## Struktur Direktori

```
FaztrackAttendance/
├── backend/
│   ├── .env.metro              # env per-tenant (mode 0600, gitignored)
│   ├── .env.lumin
│   ├── .env.acme
│   └── scripts/
│       └── seed_acme_standalone.py   # seed stub (auto-generated, idempotent)
├── scripts/
│   ├── fzctl.py                # CLI utama
│   ├── tenants.json            # registry metadata (NO secret)
│   └── templates/
│       ├── env.template
│       ├── systemd-backend.service.template
│       ├── systemd-frontend.service.template
│       └── seed_stub.py.template
├── backups/                    # dump per-tenant
├── /home/ubuntu/<slug>-frontend/  # frontend build tree per-tenant (enterprise)
└── /home/ubuntu/lumin-frontend/   # (contoh eksisting)
```

---

## Pitfalls & Catatan

1. **Frontend build terpisah WAJIB** — jangan build di `frontend/` repo, akan timpa `.next/standalone` tenant lain.
2. **`cp .next/static .next/standalone/.next/`** — Next.js standalone output tidak copy static assets, harus manual (sudah di-handle `_build_frontend`).
3. **Port auto-allocation** — `next_port()` ambil port dari registry + floor default. Floor: db=5439, backend=8085, frontend=3012 (naik dari sana kalau ada tabrakan).
4. **Secret di env file saja** — registry (`tenants.json`) hanya metadata (slug, nama, port, service, dir). Password/token DI `backend/.env.<slug>` (mode 0600).
5. **Alembic butuh cwd=backend** — `fzctl` auto-set `cwd=BACKEND_DIR`.
6. **SMB shared container** — dibuat sekali (`faztrack-smb-postgres:5438`), klien SMB berikutnya pakai container yang sama, beda database di dalamnya.

---

## Status Tenant Eksisting

| Slug | Nama | Tier | DB Port | BE Port | FE Port |
|---|---|---|---|---|---|
| metro | Metro Mining | enterprise | 5436 | 8084 | 3004 |
| lumin | Lumin Park Property | enterprise | 5437 | 8011 | 3011 |
