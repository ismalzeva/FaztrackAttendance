# 🧪 Panduan Uji Coba Absensi Metro Mining

## Informasi Login

| Field | Nilai |
|-------|-------|
| **Tenant Code** | `metro-mining` |
| **Worker Code** | `W001` |
| **PIN** | `1234` |
| **Nama** | Budi Santoso |

## Lokasi Tersedia

| Lokasi | Koordinat | Radius |
|--------|-----------|--------|
| **Lavenue** | -6.24832, 106.84326 | 500m |
| **Pakuwon** | -6.22161, 106.84330 | 500m |

## Cara Akses

### Via Browser (Mobile)
```
http://<IP_SERVER>:3004/absen
```

### Via API (curl)

#### 1. Login
```bash
curl -X POST http://<IP_SERVER>:8084/api/v1/worker-web/login \
  -H "Content-Type: application/json" \
  -d '{"tenant_code":"metro-mining","worker_code":"W001","pin":"1234"}'
```
Response: `{"data": {"access_token": "...", "worker": {"code": "W001", "name": "Budi Santoso"}}}`

#### 2. Lihat Shift Hari Ini
```bash
curl http://<IP_SERVER>:8084/api/v1/worker-web/shift \
  -H "Authorization: Bearer <TOKEN>"
```
Menampilkan project yang tersedia + status jadwal.

#### 3. Minta Challenge (sebelum absen)
```bash
curl -X POST http://<IP_SERVER>:8084/api/v1/worker-web/challenge \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"event_type":"CHECK_IN","project_id":"prj-lavenue-01"}'
```
Response berisi `challenge_id` + `challenge` (berlaku 120 detik).

#### 4. Submit Absen
```bash
curl -X POST http://<IP_SERVER>:8084/api/v1/worker-web/events \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "challenge_id": "<CHALLENGE_ID>",
    "challenge": "<CHALLENGE>",
    "event_type": "CHECK_IN",
    "project_id": "prj-lavenue-01",
    "latitude": -6.2483209917533,
    "longitude": 106.8432592379149,
    "accuracy_m": 10.0,
    "captured_at_client": "2026-08-27T08:00:00Z"
  }'
```

## Alur Lengkap (Browser)

```
1. Buka http://<IP>:3004/absen di HP
2. Masukkan PIN: 1234
3. Pilih lokasi: Lavenue atau Pakuwon
4. Klik CHECK IN
   → GPS divalidasi (harus dalam radius 500m)
   → Status: VALID ✅
5. Setelah selesai kerja, buka lagi
6. Klik CHECK OUT
   → Status: VALID ✅
```

## Aturan Bisnis

- **1 CHECK_IN per hari** — tidak bisa check-in 2x di hari yang sama
- **GPS wajib** — harus dalam radius 500m dari titik lokasi
- **Challenge berlaku 120 detik** — harus submit dalam 2 menit
- **Tanpa device binding** — absen masuk status REVIEW (verifikasi supervisor)
- **Shift**: DAY 07:00–19:00 WITA, NIGHT 19:00–07:00 WITA

## Status Response

| Status | Arti |
|--------|------|
| `VALID` | Absen diterima, GPS dalam radius |
| `REVIEW` | Absen diterima, perlu verifikasi manual (tanpa device binding) |
| `REJECTED` | GPS di luar radius / challenge expired |

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| `INVALID_CREDENTIALS` | Cek tenant_code, worker_code, PIN |
| `TENANT_NOT_FOUND` | Tenant code harus `metro-mining` |
| `CHALLENGE_EXPIRED` | Minta challenge baru (berlaku 120 detik) |
| `OUTSIDE_GEOFENCE` | Pastikan GPS dalam radius 500m dari titik lokasi |
| `ALREADY_CHECKED_IN` | Sudah ada CHECK_IN hari ini, tidak bisa 2x |
| `Internal Server Error` | Cek log: `sudo journalctl -u faztrack-attendance-metro -n 20` |

## Service Management

```bash
# Status
sudo systemctl status faztrack-attendance-metro.service

# Restart
sudo systemctl restart faztrack-attendance-metro.service

# Log
sudo journalctl -u faztrack-attendance-metro.service -f
```
