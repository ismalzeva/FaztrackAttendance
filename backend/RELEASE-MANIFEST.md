# LUMIN RELEASE MANIFEST
Generated: 2026-09-09T12:45:00Z

## Source
- Backend SHA: 413720b16b2c9e2e24223311062d63ea814f6e19 (lumin-park branch)
- Frontend SHA: 6e3a3e20a27aee0b756c3144e13f172b6430975d (main branch)
- BUILD_ID: S0kC8_NAlhQyCLKMFHHdQ

## Artifacts
- lumin-backend-413720b1.tar.gz — 58 files, 178K
  - SHA256: b8b5d890f2e0d6dfdf225740e561b9c6b2ef6854f0416c96e283936ef334bba5
- lumin-frontend-6e3a3e20.tar.gz — 177 files, 636K
  - SHA256: f96bf8805f19b555fb6f3509e57c3bc241f3cbeaa0a54b175a90f1fc71ec5740

## Chunk Hashes
- Admin: page-7ef835f4a59d5f3e.js
- Dashboard: page-18c48464202db5cb.js
- Absen: page-b864c4195106e108.js

## Runtime
- Frontend: Next.js v15.5.23 standalone (server.js)
- Backend: FastAPI + uvicorn
- Database: PostgreSQL 16

## Excluded from Artifacts
- .env, .env.lumin, .env.local
- node_modules
- .next/cache
- uploads/
- database files
- .git/

## Changes in This Release
- Absen page: remove "Kode Perusahaan", dropdown nama karyawan, hardcode tenant
- Backend: remove photo_url from AttendanceEvent constructor
- Scripts: data reconciliation, backup, deploy, rollback

## Production Target
- Path: /home/ubuntu/apps/attendance-lumin/
- DB container: attendance-lumin-postgres
- Caddy container: caddy-main
- Ports: 8011 (backend), 3011 (frontend)
