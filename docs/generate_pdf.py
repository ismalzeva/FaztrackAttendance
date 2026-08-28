#!/usr/bin/env python3
"""Generate PDF panduan klien Metro Mining."""
from fpdf import FPDF

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
OUT = "/home/ubuntu/FaztrackAttendance/docs/Panduan_Faztrack_Attendance_Metro_Mining.pdf"

# Colors
NAVY = (16, 42, 67)
AMBER = (245, 166, 35)
WHITE = (255, 255, 255)
DARK = (33, 33, 33)
GRAY = (100, 100, 100)
LIGHT_BG = (245, 245, 245)
GREEN = (39, 174, 96)
RED = (231, 76, 60)
ORANGE = (243, 156, 18)


class Guide(FPDF):
    def __init__(self):
        super().__init__()
        self.add_font("DSans", "", FONT)
        self.add_font("DSans", "B", FONT_BOLD)
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("DSans", "B", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 8, "Faztrack Attendance — Panduan Metro Mining", align="L")
        self.cell(0, 8, f"Halaman {self.page_no()}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*AMBER)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("DSans", "", 7)
        self.set_text_color(*GRAY)
        self.cell(0, 10, "Faztrack Attendance — Sistem Absensi Karyawan Berbasis Lokasi", align="C")

    def cover_page(self):
        self.add_page()
        self.ln(40)
        # Navy header bar
        self.set_fill_color(*NAVY)
        self.rect(0, 30, 210, 60, "F")
        # Amber accent
        self.set_fill_color(*AMBER)
        self.rect(0, 90, 210, 4, "F")
        # Title
        self.set_y(42)
        self.set_font("DSans", "B", 28)
        self.set_text_color(*WHITE)
        self.cell(0, 14, "FAZTRACK ATTENDANCE", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DSans", "", 14)
        self.cell(0, 10, "Panduan Penggunaan Sistem", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(30)
        # Client name
        self.set_font("DSans", "B", 20)
        self.set_text_color(*NAVY)
        self.cell(0, 12, "METRO MINING", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)
        self.set_font("DSans", "", 11)
        self.set_text_color(*GRAY)
        self.cell(0, 8, "Sistem Absensi Karyawan Berbasis Lokasi (GPS + Geofence)", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(30)
        # Info box
        self.set_fill_color(*LIGHT_BG)
        self.set_draw_color(*NAVY)
        self.set_line_width(0.3)
        x = 40
        w = 130
        self.rect(x, self.get_y(), w, 40, "FD")
        self.set_x(x + 5)
        self.set_font("DSans", "B", 10)
        self.set_text_color(*NAVY)
        self.cell(w - 10, 10, "Link Akses", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DSans", "", 9)
        self.set_text_color(*DARK)
        self.set_x(x + 5)
        self.cell(w - 10, 7, "Absensi Karyawan:", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DSans", "B", 9)
        self.set_x(x + 5)
        self.cell(w - 10, 7, "https://attendance-metro.gofaztrack.com/absen", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DSans", "", 9)
        self.set_x(x + 5)
        self.cell(w - 10, 7, "Dashboard Manajemen:", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DSans", "B", 9)
        self.set_x(x + 5)
        self.cell(w - 10, 7, "https://attendance-metro.gofaztrack.com/login", new_x="LMARGIN", new_y="NEXT")

    def section_title(self, icon, title):
        self.ln(6)
        self.set_fill_color(*NAVY)
        self.set_text_color(*WHITE)
        self.set_font("DSans", "B", 13)
        self.cell(0, 10, f"  {icon}  {title}", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(3)

    def sub_title(self, title):
        self.ln(3)
        self.set_font("DSans", "B", 11)
        self.set_text_color(*NAVY)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*AMBER)
        self.set_line_width(0.8)
        self.line(10, self.get_y(), 80, self.get_y())
        self.ln(3)

    def body_text(self, text):
        self.set_font("DSans", "", 9.5)
        self.set_text_color(*DARK)
        self.multi_cell(0, 6, text)
        self.ln(1)

    def step_list(self, steps):
        self.set_font("DSans", "", 9.5)
        self.set_text_color(*DARK)
        for i, step in enumerate(steps, 1):
            self.set_x(15)
            self.set_font("DSans", "B", 9.5)
            self.set_text_color(*AMBER)
            self.cell(8, 6, f"{i}.")
            self.set_font("DSans", "", 9.5)
            self.set_text_color(*DARK)
            self.cell(0, 6, step, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def note_box(self, items):
        self.set_fill_color(255, 249, 230)
        self.set_draw_color(*ORANGE)
        self.set_line_width(0.4)
        y_start = self.get_y()
        self.set_x(15)
        self.set_font("DSans", "B", 9.5)
        self.set_text_color(*ORANGE)
        self.cell(0, 7, "Yang Perlu Diperhatikan:", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DSans", "", 9)
        self.set_text_color(*DARK)
        for item in items:
            self.set_x(20)
            self.cell(5, 6, chr(8226))  # bullet
            self.cell(0, 6, item, new_x="LMARGIN", new_y="NEXT")
        y_end = self.get_y() + 2
        self.rect(13, y_start - 2, 184, y_end - y_start + 4, "D")
        self.set_y(y_end + 4)

    def table(self, headers, rows, col_widths=None):
        if col_widths is None:
            col_widths = [190 / len(headers)] * len(headers)
        # Header
        self.set_fill_color(*NAVY)
        self.set_text_color(*WHITE)
        self.set_font("DSans", "B", 9)
        self.set_x(10)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 8, f" {h}", border=1, fill=True)
        self.ln()
        # Rows
        self.set_font("DSans", "", 9)
        fill = False
        for row in rows:
            if fill:
                self.set_fill_color(*LIGHT_BG)
            else:
                self.set_fill_color(*WHITE)
            self.set_text_color(*DARK)
            self.set_x(10)
            for i, cell in enumerate(row):
                self.cell(col_widths[i], 7, f" {cell}", border=1, fill=True)
            self.ln()
            fill = not fill
        self.ln(3)


def build():
    pdf = Guide()

    # ── Cover ──
    pdf.cover_page()

    # ── Page 2: Karyawan ──
    pdf.add_page()
    pdf.section_title("1", "PANDUAN KARYAWAN")

    pdf.sub_title("Cara Absen Masuk (CHECK IN)")
    pdf.step_list([
        "Buka link absensi dari HP:",
        "   https://attendance-metro.gofaztrack.com/absen",
        "Masukkan ID Pekerja (contoh: W001)",
        "Masukkan PIN (4 digit)",
        "Klik tombol Masuk",
        "Pilih Lokasi Kerja (Lavenue atau Pakuwon)",
        "Klik tombol CHECK IN",
        "Izinkan akses lokasi GPS saat diminta browser",
        "Tunggu hingga muncul status VALID",
    ])

    pdf.sub_title("Cara Absen Keluar (CHECK OUT)")
    pdf.step_list([
        "Buka halaman absensi yang sama",
        "Login dengan ID Pekerja & PIN",
        "Klik tombol CHECK OUT",
        "Tunggu konfirmasi berhasil",
    ])

    pdf.note_box([
        "GPS harus aktif — izinkan akses lokasi di pengaturan browser HP",
        "Radius absensi: 500 meter dari titik lokasi kerja",
        "1x CHECK IN per hari per lokasi",
        "Jam kerja: 07:00 - 19:00 WITA",
        "Di dalam radius = status VALID",
        "Di luar radius = status REVIEW (perlu verifikasi manajemen)",
    ])

    # ── Page 3: Manajemen ──
    pdf.add_page()
    pdf.section_title("2", "PANDUAN MANAJEMEN")

    pdf.sub_title("Login Dashboard")
    pdf.step_list([
        "Buka: https://attendance-metro.gofaztrack.com/login",
        "Masukkan email dan password",
        "Klik tombol Masuk",
    ])

    pdf.sub_title("Fitur Dashboard")
    pdf.table(
        ["Fitur", "Keterangan"],
        [
            ["Daftar Karyawan", "Lihat semua karyawan & status aktif"],
            ["Riwayat Absensi", "Filter per tanggal, lokasi, status"],
            ["Status Absensi", "VALID / REVIEW / REJECTED"],
            ["Export Data", "Download laporan absensi"],
        ],
        [60, 130],
    )

    pdf.sub_title("Keterangan Status Absensi")
    pdf.table(
        ["Status", "Arti"],
        [
            ["VALID", "Absensi berhasil, dalam radius lokasi"],
            ["REVIEW", "Di luar radius, perlu verifikasi manajemen"],
            ["REJECTED", "Ditolak oleh sistem"],
        ],
        [50, 140],
    )

    # ── Page 4: Troubleshooting ──
    pdf.add_page()
    pdf.section_title("3", "TROUBLESHOOTING")

    pdf.sub_title("Masalah Umum & Solusi")
    pdf.table(
        ["Masalah", "Solusi"],
        [
            ['"Izin lokasi ditolak"', "Aktifkan GPS di pengaturan HP & izinkan browser akses lokasi"],
            ['"Sinyal GPS tidak tersedia"', "Coba di luar ruangan, pastikan GPS aktif di HP"],
            ['"Bukan jadwal kerja"', "Hubungi admin untuk mengatur jadwal kerja"],
            ['"Gagal mengirim absensi"', "Cek koneksi internet, coba lagi"],
            ["Lupa PIN", "Hubungi admin untuk reset PIN"],
            ["Halaman tidak bisa dibuka", "Cek koneksi internet, coba browser lain"],
        ],
        [60, 130],
    )

    pdf.ln(10)
    pdf.sub_title("Tips Absensi Lancar")
    pdf.step_list([
        "Pastikan GPS HP aktif sebelum membuka halaman absensi",
        "Gunakan browser Chrome atau Safari untuk hasil terbaik",
        "Absen di lokasi terbuka (bukan di dalam gedung tertutup)",
        "Jika gagal, tutup browser dan buka kembali",
        "Simpan ID Pekerja & PIN di tempat yang aman",
    ])

    pdf.output(OUT)
    print(f"PDF generated: {OUT}")


if __name__ == "__main__":
    build()
