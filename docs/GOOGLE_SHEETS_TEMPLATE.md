# Faztrack Attendance — Google Sheets Master Template

Nama spreadsheet bebas. Nama lima tab berikut harus persis sama dan baris pertama
harus berisi header. Bagikan sebagai **Anyone with the link — Viewer** agar backend
dapat membaca CSV tanpa meminta akses ke akun Google pengguna.

## Projects

| project_code | project_name | latitude | longitude | radius_m | work_start | work_end |
|---|---|---:|---:|---:|---|---|
| PRJ-01 | Lumin Park A | -6.200000 | 106.800000 | 150 | 08:00 | 17:00 |

## Workers

| worker_code | worker_name | phone | is_active |
|---|---|---|---|
| EMP-001 | Ahmad | 081234567890 | TRUE |

## Assignments

Satu `worker_code` hanya boleh memiliki satu proyek pada tanggal yang sama.

| worker_code | project_code | work_date |
|---|---|---|
| EMP-001 | PRJ-01 | 2026-08-17 |

## Schedules

| worker_code | work_date | start_time | end_time | is_working_day |
|---|---|---|---|---|
| EMP-001 | 2026-08-17 | 08:00 | 17:00 | TRUE |

## Supervisors

`login_id` harus sudah terdaftar sebagai role `SUPERVISOR`. Satu supervisor boleh
muncul pada beberapa proyek.

| login_id | project_code |
|---|---|
| supervisor.1 | PRJ-01 |

## Aturan impor

- Format tanggal: `YYYY-MM-DD`.
- Format jam: `HH:MM` 24 jam.
- `radius_m` minimal 25 meter.
- Preview dengan error tidak dapat dikonfirmasi.
- `project_code` dan `worker_code` adalah kunci tetap per perusahaan.
- Mengimpor ulang kode yang sama memperbarui data dan tidak membuat duplikasi.
