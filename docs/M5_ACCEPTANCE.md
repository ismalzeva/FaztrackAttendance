# M5 Acceptance — Pilot Readiness

- [x] Admin/supervisor mempunyai halaman login dan pemilihan workspace.
- [x] Frontend dapat dibuild sebagai image standalone.
- [x] Backend production menolak JWT secret lemah.
- [x] PostgreSQL, backend, dan frontend mempunyai orkestrasi container.
- [x] Migrasi database dijalankan sebelum API menerima trafik.
- [x] Readiness check memastikan koneksi database tersedia.
- [x] Nilai sensitif dipasok melalui environment dan tidak ditulis dalam image.
- [x] Runbook deployment dan bootstrap pilot tersedia.
- [x] UAT mencakup perangkat, geofence, duplikasi, urutan event, review, timesheet, dan closing.
- [x] Kriteria GO/NO-GO rollout 50 pekerja telah ditetapkan.

M5 menyatakan aplikasi siap dipasang, bukan menyatakan server publik telah dideploy. Deployment aktual memerlukan hostname, DNS/TLS, dan akses VPS milik pengguna.
