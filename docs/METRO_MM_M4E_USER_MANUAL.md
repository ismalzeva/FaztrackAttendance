# 📘 Faztrack Attendance — Panduan Pengguna Metro Mining (M4E)

**Versi:** 1.0  
**Tanggal:** 22 Agustus 2026  
**Status:** Pilot Instance — Metro Mining  
**Dokumen:** METRO_MM_M4E_USER_MANUAL.md

---

## Daftar Isi

1. [Ikhtisar Sistem](#1-ikhtisar-sistem)
2. [Akses Sistem](#2-akses-sistem)
3. [Login & Autentikasi](#3-login--autentikasi)
4. [Navigasi Halaman](#4-navigasi-halaman)
5. [Halaman Dashboard](#5-halaman-dashboard)
6. [Halaman Devices](#6-halaman-devices)
7. [Halaman GPS Review](#7-halaman-gps-review)
8. [Halaman Timesheets](#8-halaman-timesheets)
9. [Halaman Attendance](#9-halaman-attendance)
10. [API Endpoints](#10-api-endpoints)
11. [Data Metro Mining](#11-data-metro-mining)
12. [Keterbatasan yang Diketahui](#12-keterbatasan-yang-diketahui)
13. [FAQ & Pemecahan Masalah](#13-faq--pemecahan-masalah)

---

## 1. Ikhtisar Sistem

**Faztrack Attendance** adalah sistem manajemen kehadiran dan penjadwalan tenaga kerja yang dirancang untuk operasi pertambangan. Sistem ini menyediakan:

- Pelacakan kehadiran pekerja berbasis shift
- Persetujuan perangkat (HP) untuk absensi
- Review bukti lokasi GPS
- Rekap timesheet dan laporan kehadiran
- Dashboard ringkasan data master

**Metro Mining** adalah instance pilot yang terisolasi dari sistem Faztrack Attendance. Instance ini digunakan untuk pengujian dan demonstrasi sebelum deployment penuh ke lingkungan produksi.

### Fitur Utama (Milestone M4E)

| Milestone | Fitur | Status |
|-----------|-------|--------|
| M4A | Dashboard & Snapshot | ✅ API Only |
| M4B | Roster & Operasional | ✅ API Only |
| M4C | Exceptions & Keputusan | ✅ API Only |
| M4D | Laporan & Export | ✅ API Only |
| M4E | Frontend Pages | ✅ Tersedia |

> **Catatan:** Milestone M4A–M4D tersedia melalui API saja (tanpa halaman frontend). Halaman frontend tersedia mulai M4E.

---

## 2. Akses Sistem

### URL Produksi (Belum Aktif)

```
https://attendance-metro.gofaztrack.com
```

> ⚠️ **Status:** URL produksi belum aktif sampai DNS dikonfigurasi. Saat ini hanya dapat diakses melalui URL lokal.

### URL Lokal (VPS)

```
http://localhost:3004
```

Untuk mengakses dari luar VPS, gunakan IP server diikuti port `3004`:

```
http://<IP_VPS>:3004
```

### Persyaratan Teknis

- **Browser:** Chrome 90+, Firefox 88+, Edge 90+, Safari 14+
- **Koneksi:** Stabil (latency < 500ms)
- **Resolusi:** Minimal 1280×720

---

## 3. Login & Autentikasi

### Halaman Login

Buka URL sistem di browser, Anda akan diarahkan ke halaman login.

[SCREENSHOT: Halaman login Faztrack Attendance dengan field email dan password]

### Akun yang Tersedia

| Role | Email | Password | Akses |
|------|-------|----------|-------|
| **Admin / Owner** | `admin@metro-mining.id` | `MetroDemo2026!` | Penuh — semua halaman dan API |
| **Supervisor** | `supervisor@metro-mining.id` | `MetroDemo2026!` | Terbatas — review dan god mode |

### Cara Login

1. Buka halaman login di browser
2. Masukkan **email** pada field pertama
3. Masukkan **password** pada field kedua
4. Klik tombol **"Masuk"** atau tekan `Enter`
5. Anda akan diarahkan ke halaman Dashboard

[SCREENSHOT: Proses login dengan kredensial yang diisi]

### Logout

Klik menu profil di pojok kanan atas, lalu pilih **"Keluar"**.

---

## 4. Navigasi Halaman

Setelah login, Anda akan melihat sidebar navigasi di sisi kiri halaman.

[SCREENSHOT: Sidebar navigasi dengan menu-menu yang tersedia]

### Menu Navigasi

| Menu | URL | Deskripsi |
|------|-----|-----------|
| **Dashboard** | `/` | Ringkasan data master dan overview sistem |
| **Devices** | `/devices` | Persetujuan perangkat HP untuk absensi |
| **GPS Review** | `/review` | Review bukti lokasi GPS pekerja |
| **Timesheets** | `/timesheets` | Rekap kehadiran per pekerja per periode |
| **Attendance** | `/attendance` | Input dan kelola absensi pekerja |

---

## 5. Halaman Dashboard

**URL:** `/`

Halaman Dashboard menampilkan ringkasan data master Metro Mining.

[SCREENSHOT: Halaman Dashboard dengan kartu ringkasan data]

### Informasi yang Ditampilkan

- **Jumlah Pekerja:** Total pekerja aktif
- **Jumlah Shift:** Shift yang dikonfigurasi
- **Jumlah Crew:** Crew yang tersedia
- **Jumlah Equipment:** Alat berat terdaftar
- **Jumlah Site:** Site operasional

### Fitur Dashboard

- Ringkasan data master dalam bentuk kartu
- Navigasi cepat ke halaman lain
- Status sistem terkini

---

## 6. Halaman Devices

**URL:** `/devices`

Halaman Devices digunakan untuk mengelola persetujuan perangkat HP yang digunakan pekerja untuk melakukan absensi.

[SCREENSHOT: Halaman Devices dengan daftar perangkat]

### Fitur Utama

- **Daftar Perangkat:** Melihat semua HP yang terdaftar
- **Persetujuan:** Menyetujui atau menolak perangkat baru
- **Status:** Memantau status perangkat (aktif/nonaktif)

### Cara Menyetujui Perangkat

1. Buka halaman **Devices**
2. Cari perangkat yang ingin disetujui
3. Klik tombol **"Setujui"** pada baris perangkat
4. Perangkat akan berubah status menjadi **Aktif**

[SCREENSHOT: Dialog persetujuan perangkat]

---

## 7. Halaman GPS Review

**URL:** `/review`

Halaman GPS Review digunakan untuk memverifikasi bukti lokasi GPS yang dikirimkan pekerja saat melakukan absensi.

[SCREENSHOT: Halaman GPS Review dengan peta dan daftar review]

### Fitur Utama

- **Peta Interaktif:** Menampilkan lokasi absensi pada peta
- **Daftar Review:** Daftar absensi yang perlu diverifikasi
- **Keputusan:** Menyetujui atau menolak bukti lokasi

### Cara Review GPS

1. Buka halaman **GPS Review**
2. Pilih absensi yang ingin direview
3. Periksa lokasi pada peta
4. Klik **"Setujui"** jika lokasi valid, atau **"Tolak"** jika tidak valid

[SCREENSHOT: Detail review GPS dengan peta dan tombol keputusan]

---

## 8. Halaman Timesheets

**URL:** `/timesheets`

Halaman Timesheets menampilkan rekap kehadiran pekerja dalam format timesheet.

[SCREENSHOT: Halaman Timesheets dengan tabel rekap kehadiran]

### Fitur Utama

- **Filter Periode:** Pilih rentang tanggal untuk rekap
- **Filter Pekerja:** Filter berdasarkan nama atau role
- **Export:** Unduh rekap dalam format CSV atau XLSX
- **Detail Klik:** Klik baris untuk melihat detail kehadiran

### Cara Menggunakan Timesheets

1. Buka halaman **Timesheets**
2. Atur **filter periode** (tanggal mulai dan akhir)
3. (Opsional) Filter berdasarkan **pekerja** atau **role**
4. Klik **"Terapkan Filter"**
5. Lihat rekap kehadiran pada tabel
6. Klik **"Export"** untuk mengunduh laporan

[SCREENSHOT: Filter timesheet dan hasil rekap]

---

## 9. Halaman Attendance

**URL:** `/attendance`

Halaman Attendance digunakan untuk mengelola absensi pekerja secara langsung.

[SCREENSHOT: Halaman Attendance dengan form input absensi]

### Fitur Utama

- **Input Absensi:** Catat kehadiran pekerja secara manual
- **Daftar Absensi:** Lihat riwayat absensi hari ini
- **Status Kehadiran:** Hadir, Terlambat, Absen, Cuti

### Cara Input Absensi

1. Buka halaman **Attendance**
2. Pilih **pekerja** dari dropdown
3. Pilih **shift** (DAY atau NIGHT)
4. Masukkan **waktu masuk** dan **waktu keluar**
5. Klik **"Simpan"**

[SCREENSHOT: Form input absensi yang sudah diisi]

---

## 10. API Endpoints

Sistem menyediakan REST API yang dapat diakses melalui base URL `/api/v1/`.

### Autentikasi

| Endpoint | Method | Deskripsi |
|----------|--------|-----------|
| `/api/v1/auth/login` | POST | Login dan mendapatkan token JWT |
| `/api/v1/auth/me` | GET | Mendapatkan informasi user yang sedang login |

**Contoh Request Login:**

```bash
curl -X POST http://localhost:3004/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@metro-mining.id", "password": "MetroDemo2026!"}'
```

**Response:**

```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "user": {
    "id": "...",
    "email": "admin@metro-mining.id",
    "role": "admin"
  }
}
```

### Dashboard

| Endpoint | Method | Deskripsi |
|----------|--------|-----------|
| `/api/v1/dashboard/snapshot` | GET | Ringkasan data master (pekerja, shift, crew, equipment, site) |

### Roster

| Endpoint | Method | Deskripsi |
|----------|--------|-----------|
| `/api/v1/roster/operational` | GET | Jadwal operasional roster |

### Exceptions

| Endpoint | Method | Deskripsi |
|----------|--------|-----------|
| `/api/v1/exceptions` | GET | Daftar semua exception |
| `/api/v1/exceptions/:id` | GET | Detail exception berdasarkan ID |
| `/api/v1/exceptions/:id/decisions` | POST | Submit keputusan untuk exception |

### Reports

| Endpoint | Method | Deskripsi | Format |
|----------|--------|-----------|--------|
| `/api/v1/reports/shift-attendance` | GET | Laporan kehadiran per shift | JSON, CSV, XLSX |
| `/api/v1/reports/exceptions` | GET | Laporan exception | JSON, CSV, XLSX |
| `/api/v1/reports/roster-vs-actual` | GET | Perbandingan roster vs kehadiran aktual | JSON, CSV, XLSX |

**Contoh Request dengan Export:**

```bash
# Export laporan kehadiran shift dalam format XLSX
curl -X GET "http://localhost:3004/api/v1/reports/shift-attendance?format=xlsx" \
  -H "Authorization: Bearer <TOKEN>" \
  --output shift-attendance.xlsx
```

### Autentikasi API

Semua endpoint (kecuali `/auth/login`) memerlukan header `Authorization`:

```
Authorization: Bearer <token>
```

---

## 11. Data Metro Mining

### Site

| Nama | Lokasi | Status |
|------|--------|--------|
| **Padang Mine** | Padang, Sumatera Barat | Aktif |

### Shift

| Nama | Jam Kerja | Zona Waktu |
|------|-----------|------------|
| **DAY** | 07:00 – 19:00 | WITA |
| **NIGHT** | 19:00 – 07:00 | WITA |

### Pekerja (12 orang)

| # | Nama | Role | Crew |
|---|------|------|------|
| 1 | Pekerja 01 | Excavator Operator | Alpha |
| 2 | Pekerja 02 | Excavator Operator | Alpha |
| 3 | Pekerja 03 | Excavator Operator | Alpha |
| 4 | Pekerja 04 | Excavator Operator | Alpha |
| 5 | Pekerja 05 | Excavator Operator | Alpha |
| 6 | Pekerja 06 | Excavator Operator | Alpha |
| 7 | Pekerja 07 | Dump Truck Operator | Bravo |
| 8 | Pekerja 08 | Dump Truck Operator | Bravo |
| 9 | Pekerja 09 | Dump Truck Operator | Bravo |
| 10 | Pekerja 10 | Dump Truck Operator | Bravo |
| 11 | Pekerja 11 | Dump Truck Operator | Bravo |
| 12 | Pekerja 12 | Dump Truck Operator | Bravo |

### Role

| Role | Jumlah | Deskripsi |
|------|--------|-----------|
| **Excavator Operator** | 6 | Operator alat berat excavator |
| **Dump Truck Operator** | 6 | Operator truk tambang |

### Crew

| Crew | Jumlah Anggota | Shift |
|------|----------------|-------|
| **Alpha** | 6 | DAY / NIGHT (rotasi) |
| **Bravo** | 6 | DAY / NIGHT (rotasi) |

### Equipment

| Kode | Tipe | Status |
|------|------|--------|
| **EX-025** | Excavator | Aktif |
| **EX-031** | Excavator | Aktif |
| **DT-014** | Dump Truck | Aktif |

---

## 12. Keterbatasan yang Diketahui

### Fitur API Only (Tanpa Frontend)

Fitur-fitur berikut hanya tersedia melalui API dan **belum memiliki halaman frontend**:

| Milestone | Fitur | Endpoint |
|-----------|-------|----------|
| M4A | Dashboard Snapshot | `/api/v1/dashboard/snapshot` |
| M4B | Roster Operasional | `/api/v1/roster/operational` |
| M4C | Exceptions & Keputusan | `/api/v1/exceptions/*` |
| M4D | Laporan & Export | `/api/v1/reports/*` |

Untuk mengakses fitur ini, gunakan tools seperti `curl`, Postman, atau integrasi aplikasi lain.

### DNS Belum Dikonfigurasi

URL produksi `https://attendance-metro.gofaztrack.com` belum aktif. Saat ini sistem hanya dapat diakses melalui:

- `http://localhost:3004` (dari VPS)
- `http://<IP_VPS>:3004` (dari jaringan lokal)

### Geofence Data

Data geofence (area virtual untuk validasi lokasi) **belum dikonfigurasi** (TBC). Fitur GPS Review akan berfungsi penuh setelah geofence ditetapkan.

### Minimum Rest Hours

Aturan jam istirahat minimum antar shift **belum dikonfigurasi** (TBC). Sistem saat ini tidak memvalidasi jeda waktu antara shift malam dan shift siang.

---

## 13. FAQ & Pemecahan Masalah

### T: Saya tidak bisa login

**J:** Pastikan:
1. Email dan password benar (perhatikan huruf besar/kecil)
2. Sistem sedang berjalan (cek `http://localhost:3004`)
3. Browser tidak memblokir cookie

### T: Halaman kosong setelah login

**J:** 
1. Coba refresh halaman (`F5` atau `Ctrl+R`)
2. Bersihkan cache browser
3. Cek console browser untuk error (`F12` → tab Console)

### T: API mengembalikan 401 Unauthorized

**J:**
1. Token JWT mungkin sudah kedaluwarsa, login ulang
2. Pastikan header `Authorization` benar: `Bearer <token>`
3. Pastikan menggunakan endpoint `/api/v1/auth/login` untuk mendapatkan token baru

### T: Data tidak muncul di Dashboard

**J:**
1. Pastikan Anda login dengan akun yang memiliki akses
2. Cek apakah data sudah di-seed ke database
3. Gunakan endpoint `/api/v1/dashboard/snapshot` untuk verifikasi data via API

### T: Export laporan gagal

**J:**
1. Pastikan parameter `format` benar (`csv` atau `xlsx`)
2. Cek apakah ada data untuk periode yang dipilih
3. Gunakan header `Accept` yang sesuai (`text/csv` atau `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`)

---

## Lampiran

### A. Struktur URL

```
http://localhost:3004/
├── /                          # Dashboard
├── /devices                   # Devices
├── /review                    # GPS Review
├── /timesheets                # Timesheets
├── /attendance                # Attendance
└── /api/v1/
    ├── /auth/login            # POST - Login
    ├── /auth/me               # GET - User info
    ├── /dashboard/snapshot    # GET - Dashboard data
    ├── /roster/operational    # GET - Roster
    ├── /exceptions            # GET - List exceptions
    ├── /exceptions/:id        # GET - Detail exception
    ├── /exceptions/:id/decisions  # POST - Submit decision
    ├── /reports/shift-attendance  # GET - Shift report
    ├── /reports/exceptions        # GET - Exception report
    └── /reports/roster-vs-actual  # GET - Roster vs actual
```

### B. Format Tanggal

Sistem menggunakan format:
- **Tanggal:** `YYYY-MM-DD` (contoh: `2026-08-22`)
- **Waktu:** `HH:MM` (contoh: `07:00`)
- **Zona Waktu:** WITA (UTC+8)

### C. Kontak Dukungan

Untuk pertanyaan atau masalah teknis terkait Faztrack Attendance Metro Mining:

- **Email:** support@gofaztrack.com
- **Dokumentasi API:** Tersedia di `/api/v1/docs` (jika dikonfigurasi)

---

**Dokumen ini merupakan bagian dari proyek Faztrack Attendance — Metro Mining Pilot Instance.**  
**© 2026 Faztrack Consulting. All rights reserved.**
