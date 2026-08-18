# M5 Pilot Runbook — Lumin Park Property

Runbook ini membawa build M0–M4 ke satu pilot operasional. Nama tenant, proyek, pekerja, lokasi, jadwal, dan struktur supervisor tetap dapat diganti melalui Google Sheets.

## 1. Prasyarat server

- Satu VPS Linux dengan Docker Engine dan Docker Compose.
- Dua hostname HTTPS: satu untuk PWA dan satu untuk API.
- Reverse proxy/TLS berada di depan port `3000` dan `8000`.
- Google Sheet pilot dengan lima tab sesuai `GOOGLE_SHEETS_TEMPLATE.md` dan akses baca melalui link.
- Lokasi pusat setiap proyek sudah diuji di titik check-in sebenarnya.

Rekomendasi awal untuk 50 pekerja dan sekitar 7 proyek: 2 vCPU, RAM 4 GB, SSD 40 GB, serta backup volume PostgreSQL harian. Ini adalah baseline pilot, bukan sizing final.

## 2. Konfigurasi

Salin `.env.production.example` menjadi `.env`, lalu isi:

- `POSTGRES_PASSWORD`: password database unik.
- `FAZTRACK_JWT_SECRET`: random secret minimal 32 karakter.
- `APP_ORIGIN`: origin PWA HTTPS, tanpa path.
- `PUBLIC_API_URL`: URL publik API yang berakhir `/api/v1`.
- `APP_DOMAIN`: hostname PWA tanpa `https://`.
- `API_DOMAIN`: hostname API tanpa `https://`.
- `FAZTRACK_DEMO_SEED_PASSWORD`: password sementara bootstrap admin/supervisor.
- `FAZTRACK_DEMO_WORKER_PIN`: PIN sementara untuk aktivasi pekerja pilot.

Jangan menyimpan `.env` di repository atau mengirimkannya melalui grup chat.

## 3. Jalankan aplikasi

```bash
docker compose up -d --build
docker compose ps
docker compose logs backend --tail=100
```

Backend menjalankan `alembic upgrade head` sebelum API dimulai. Health check baru hijau setelah database dapat diakses. Caddy menerbitkan sertifikat HTTPS otomatis setelah kedua DNS A record mengarah ke VPS.

Verifikasi tanpa mengubah data:

```bash
FAZTRACK_SMOKE_API_URL=https://api-attendance.example.com \
  docker compose exec backend python scripts/pilot_smoke.py
```

Jika script tidak tersedia di image runtime, gunakan:

```bash
curl -f https://api-attendance.example.com/health/live
curl -f https://api-attendance.example.com/health/ready
```

## 4. Bootstrap data pilot

Script bawaan menggunakan synthetic Google Sheet Lumin Park yang telah tervalidasi. Jalankan satu kali:

```bash
docker compose run --rm backend python scripts/load_demo_pilot.py
```

Hasil yang harus terlihat: 7 proyek, 50 pekerja, penempatan, jadwal, dan relasi supervisor. Setelah bootstrap:

1. Login admin di `/login`.
2. Impor Google Sheet aktual melalui halaman Master Data.
3. Periksa preview; jangan konfirmasi jika ada error.
4. Konfirmasi impor.
5. Kosongkan `FAZTRACK_DEMO_SEED_PASSWORD` dan `FAZTRACK_DEMO_WORKER_PIN` dari environment, lalu restart service.

## 5. Aktivasi lapangan

Aktivasi dilakukan per pekerja dengan supervisor hadir:

1. Supervisor memeriksa ID pekerja dan HP yang akan digunakan.
2. Pekerja membuka `/enroll`, memasukkan kode perusahaan, ID pekerja, dan PIN.
3. HP membuat kunci perangkat; tidak ada tanda tangan yang digambar.
4. Supervisor menyetujui permintaan pada `/devices`.
5. Pekerja membuka `/attendance` dan melakukan uji check-in.
6. Bila bukti masuk `REVIEW`, supervisor memeriksa `/review`.

Biometrik wajah/sidik jari tidak termasuk pilot ini.

## 6. UAT lapangan wajib

Lakukan pada satu proyek dan lima pekerja terlebih dahulu.

| ID | Skenario | Hasil yang diterima |
|---|---|---|
| UAT-01 | HP terdaftar, GPS di dalam radius | Check-in `VALID` |
| UAT-02 | PIN pekerja dipakai dari HP lain | Ditolak sebagai perangkat baru/belum terdaftar |
| UAT-03 | Check-in jauh di luar geofence | `REJECTED` |
| UAT-04 | Posisi dekat batas dengan akurasi lemah | `REVIEW`, lalu diputuskan supervisor |
| UAT-05 | Check-in kedua pada hari yang sama | Ditolak sebagai duplikasi |
| UAT-06 | Check-out tanpa check-in | Ditolak |
| UAT-07 | Check-in dan check-out valid | Timesheet menjadi `PRESENT` |
| UAT-08 | Check-in tanpa check-out pada hari lampau | Timesheet menjadi `INCOMPLETE` |
| UAT-09 | Tidak ada check-in pada hari terjadwal yang sudah lewat | Timesheet menjadi `ABSENT` |
| UAT-10 | Periode masih mempunyai incomplete/review | Tidak dapat dikunci |
| UAT-11 | Semua exception selesai | Periode dapat dikunci dan retry idempotent |

Pilot dilanjutkan ke 50 pekerja hanya setelah 11 skenario ini lulus dan koordinat/radius proyek dinyatakan benar oleh site supervisor.

## 7. Operasi harian pilot

- 07.30: supervisor memastikan pekerja dan proyek hari itu sudah sesuai jadwal.
- 08.00–09.00: pantau bukti masuk dan antrean review.
- 16.30–18.00: pantau check-out dan selesaikan exception.
- Hari berikutnya: periksa `ABSENT` dan `INCOMPLETE` pada timesheet.
- Akhir periode: selesaikan exception, lalu kunci timesheet.

## 8. Go/no-go

Pilot dinyatakan **GO** apabila:

- 5 pekerja awal menyelesaikan aktivasi tanpa bantuan teknis langsung;
- minimal 95% percobaan normal menghasilkan bukti yang dapat diputuskan;
- tidak ada absensi dari perangkat yang tidak disetujui;
- supervisor dapat menyelesaikan review dan membuka timesheet;
- data satu hari cocok dengan pemeriksaan manual supervisor.

Jika koordinat/radius salah, device enrollment gagal massal, atau timesheet berbeda dari pemeriksaan manual, statusnya **NO-GO** dan rollout 50 pekerja ditahan sampai penyebab diperbaiki.
