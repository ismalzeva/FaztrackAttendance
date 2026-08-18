"use client";

import {useEffect, useState} from "react";

type Project = {id: string; code: string; name: string};
type Row = {
  work_date: string;
  worker_code: string;
  worker_name: string;
  project_name: string;
  state: "PRESENT" | "ABSENT" | "INCOMPLETE" | "EXCEPTION" | "PENDING";
  scheduled_start: string;
  scheduled_end: string;
  check_in: string | null;
  check_out: string | null;
  late_minutes: number;
  early_leave_minutes: number;
};
type Report = {
  summary: {
    scheduled_days: number;
    present_days: number;
    absent_days: number;
    incomplete_days: number;
    exception_days: number;
    pending_days: number;
    late_days: number;
    early_leave_days: number;
    attendance_factor: number;
  };
  rows: Row[];
};

const api = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const stateLabel: Record<Row["state"], string> = {
  PRESENT: "Hadir",
  ABSENT: "Bolos",
  INCOMPLETE: "Belum lengkap",
  EXCEPTION: "Perlu review",
  PENDING: "Menunggu",
};
const dateValue = (date: Date) => date.toISOString().slice(0, 10);
const localTime = (value: string | null) => value ? new Date(value).toLocaleTimeString("id-ID", {hour: "2-digit", minute: "2-digit"}) : "—";

export default function Timesheets() {
  const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
  const weekAgo = new Date(yesterday); weekAgo.setDate(weekAgo.getDate() - 6);
  const [dateFrom, setDateFrom] = useState(dateValue(weekAgo));
  const [dateTo, setDateTo] = useState(dateValue(yesterday));
  const [projectId, setProjectId] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  function headers() {
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${localStorage.getItem("faztrack_token") ?? ""}`,
      "X-Tenant-ID": localStorage.getItem("faztrack_tenant_id") ?? "",
    };
  }

  async function loadProjects() {
    const response = await fetch(`${api}/timesheets/projects`, {headers: headers()});
    if (response.ok) setProjects((await response.json()).data);
  }

  async function loadReport() {
    setBusy(true); setMessage("");
    const params = new URLSearchParams({date_from: dateFrom, date_to: dateTo});
    if (projectId) params.set("project_id", projectId);
    try {
      const response = await fetch(`${api}/timesheets?${params}`, {headers: headers()});
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.code ?? "Rekap gagal dimuat");
      setReport(body.data);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Rekap gagal dimuat");
    } finally { setBusy(false); }
  }

  async function closePeriod() {
    if (!confirm("Kunci periode ini? Snapshot tidak akan berubah setelah dikunci.")) return;
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${api}/timesheet-periods/close`, {
        method: "POST", headers: headers(),
        body: JSON.stringify({date_from: dateFrom, date_to: dateTo, project_id: projectId || null}),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.code ?? "Periode gagal dikunci");
      setMessage(body.data.idempotent ? "Periode ini sudah terkunci." : "Periode berhasil dikunci dan snapshot tersimpan.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Periode gagal dikunci");
    } finally { setBusy(false); }
  }

  useEffect(() => { loadProjects(); }, []);

  const summary = report?.summary;
  const blockers = (summary?.incomplete_days ?? 0) + (summary?.exception_days ?? 0) + (summary?.pending_days ?? 0);
  return <main className="shell">
    <aside><div className="brand">Faztrack <span>Attendance</span></div><nav><a href="/">Master Data</a><a href="/devices">Persetujuan HP</a><a href="/review">Review GPS</a><a className="active" href="/timesheets">Rekap Kehadiran</a><a href="/attendance">Absensi Pekerja</a></nav><small>Supervisor workspace</small></aside>
    <section className="workspace">
      <header><div><p className="eyebrow">M4 · Quantification</p><h1>Timesheet lapangan</h1><p>Rekap bukti hadir menjadi angka yang siap dipakai oleh proses gaji.</p></div><span className="phase">Bukti → Rekap → Kunci</span></header>
      <article className="panel filters">
        <label><span>Dari</span><input type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)}/></label>
        <label><span>Sampai</span><input type="date" value={dateTo} onChange={event => setDateTo(event.target.value)}/></label>
        <label><span>Proyek</span><select value={projectId} onChange={event => setProjectId(event.target.value)}><option value="">Semua proyek</option>{projects.map(project => <option value={project.id} key={project.id}>{project.code} · {project.name}</option>)}</select></label>
        <button onClick={loadReport} disabled={busy}>{busy ? "Memuat…" : "Tampilkan"}</button>
      </article>
      {summary && <>
        <div className="metrics timesheetMetrics"><article><span>Hari kerja</span><strong>{summary.scheduled_days}</strong></article><article><span>Hadir</span><strong>{summary.present_days}</strong></article><article><span>Bolos</span><strong>{summary.absent_days}</strong></article><article><span>Faktor kehadiran</span><strong>{(summary.attendance_factor * 100).toLocaleString("id-ID", {maximumFractionDigits: 2})}%</strong></article></div>
        <article className="panel"><div className="panelHead"><div><h2>Rincian harian</h2><p>Belum lengkap {summary.incomplete_days} · Review {summary.exception_days} · Terlambat {summary.late_days} · Pulang awal {summary.early_leave_days}</p></div><button onClick={closePeriod} disabled={busy || blockers > 0 || summary.scheduled_days === 0}>Kunci periode</button></div>
          <div className="tableWrap"><table><thead><tr><th>Tanggal</th><th>Pekerja</th><th>Proyek</th><th>Status</th><th>Masuk</th><th>Keluar</th><th>Terlambat</th><th>Pulang awal</th></tr></thead><tbody>{report?.rows.map(row => <tr key={`${row.work_date}-${row.worker_code}`}><td>{new Date(`${row.work_date}T00:00:00`).toLocaleDateString("id-ID")}</td><td><b>{row.worker_name}</b><small>{row.worker_code}</small></td><td>{row.project_name}</td><td><span className={`state state-${row.state.toLowerCase()}`}>{stateLabel[row.state]}</span></td><td>{localTime(row.check_in)}</td><td>{localTime(row.check_out)}</td><td>{row.late_minutes ? `${row.late_minutes} mnt` : "—"}</td><td>{row.early_leave_minutes ? `${row.early_leave_minutes} mnt` : "—"}</td></tr>)}</tbody></table></div>
        </article>
        <p className="footnote">Faktor kehadiran = hari hadir ÷ hari kerja terjadwal. Contoh integrasi kemudian: G × faktor kehadiran.</p>
      </>}
      {message && <p className={message.includes("berhasil") || message.includes("sudah") ? "notice" : "error"}>{message}</p>}
    </section>
  </main>;
}
