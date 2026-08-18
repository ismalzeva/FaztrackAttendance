# M4 Acceptance — Timesheet & Quantification

## Keputusan yang dikunci

- Unit kuantifikasi adalah satu hari kerja terjadwal per pekerja.
- Status harian: `PRESENT`, `ABSENT`, `INCOMPLETE`, `EXCEPTION`, atau `PENDING`.
- `PRESENT` memerlukan check-in dan check-out berstatus `VALID`.
- `ABSENT` berarti hari terjadwal sudah lewat tanpa check-in valid.
- `INCOMPLETE` berarti ada check-in valid tetapi belum ada check-out valid.
- `EXCEPTION` berarti masih ada bukti kehadiran berstatus `REVIEW`.
- Faktor kehadiran = jumlah hari `PRESENT` dibagi jumlah hari kerja terjadwal.
- Keterlambatan dan pulang awal dihitung terhadap jadwal pekerja; toleransinya dapat diatur per perusahaan.
- M4 tidak menghitung nominal gaji. Faktor dapat menjadi input formula sederhana seperti `G × faktor kehadiran`.

## Kriteria penerimaan

- [x] Admin dan supervisor dapat memilih periode dan proyek sesuai scope.
- [x] Sistem menampilkan hari kerja, hadir, bolos, belum lengkap, exception, pending, terlambat, dan pulang awal.
- [x] Sistem menampilkan rincian bukti masuk/keluar untuk setiap pekerja dan tanggal.
- [x] Supervisor hanya melihat proyek yang ditugaskan kepadanya.
- [x] Periode hari ini atau masa depan tidak dapat dikunci.
- [x] Periode dengan `INCOMPLETE`, `EXCEPTION`, atau `PENDING` tidak dapat dikunci.
- [x] Periode kosong tidak dapat dikunci.
- [x] Penutupan menyimpan snapshot serta hash SHA-256 dan aman diulang tanpa membuat duplikasi.
- [x] Penutupan dan perubahan kebijakan tercatat dalam audit log.
- [x] Seluruh 21 test backend lulus.
- [x] Migrasi baru berhasil sampai `0005_m4_timesheet_quantification`.
- [x] Build produksi PWA lulus dan menyediakan route `/timesheets`.

## Batas MVP

Perubahan setelah periode terkunci tidak mengubah snapshot periode tersebut. Koreksi payroll, komponen upah, lembur, potongan, tunjangan, pajak, dan pembayaran berada di luar M4 dan dapat dibangun sebagai modul terpisah.
