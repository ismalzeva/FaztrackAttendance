"use client";
import {useEffect, useState} from "react";
import Shell, {ErrorBox} from "../metro-shell";
import {apiFetch, stateClass, stateLabel} from "../metro-api";

type Snapshot = {
  context: {operating_date: string; timezone: string; generated_at: string};
  shift_summary: {
    scheduled_work: number; scheduled_rest: number; scheduled_offsite: number;
    present_operational: number; not_yet_confirmed: number;
    unresolved_exceptions: number; pending_decisions: number;
  };
  roster_status: {employee_name: string; role_name: string; crew_name: string; work_status: string;
    operational_state: string; attention_badge: string | null}[];
  equipment_status: {employee_name: string; plan_display: string; actual_display: string;
    comparison_result: string; has_pending_decision: boolean}[];
  active_exceptions: {exception_id: string; exception_type: string; severity: string; status: string;
    employee_name: string; detected_at: string}[];
  action_required: {category: string; description: string; exception_id: string; severity: string}[];
};

export default function Dashboard() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    apiFetch("/dashboard/snapshot?operating_date=2026-09-01")
      .then(body => setSnap(body.data))
      .catch(err => setError(err.message));
  }, []);
  const s = snap?.shift_summary;

  return <Shell active="/dashboard" eyebrow="Metro Mining · Dasbor operasional" title="Dasbor Harian"
      subtitle="Ringkasan tenaga kerja, kehadiran, dan exception untuk tanggal operasi demo (01 Sep 2026)."
      badge={s && <span className="phase">{s.present_operational}/{s.scheduled_work} hadir</span>}>
    {error && <ErrorBox message={error}/>}
    {s && <>
      <div className="metrics">
        <article><span>Pekerja kerja (terjadwal)</span><strong>{s.scheduled_work}</strong></article>
        <article><span>Pekerja istirahat</span><strong>{s.scheduled_rest}</strong></article>
        <article><span>Di luar site (offsite)</span><strong>{s.scheduled_offsite}</strong></article>
        <article><span>Hadir operasional</span><strong>{s.present_operational}</strong></article>
        <article><span>Belum absen</span><strong>{s.not_yet_confirmed}</strong></article>
        <article><span>Exception terbuka</span><strong>{s.unresolved_exceptions}</strong></article>
        <article><span>Keputusan tertunda</span><strong>{s.pending_decisions}</strong></article>
        <article><span>Zona waktu</span><strong>WITA</strong></article>
      </div>

      {snap!.action_required.length > 0 && <article className="panel">
        <h2>Perlu tindakan</h2>
        <p>Tindakan yang menunggu respons supervisor / admin.</p>
        <div className="tableWrap"><table>
          <thead><tr><th>Kategori</th><th>Keterangan</th><th>Prioritas</th></tr></thead>
          <tbody>{snap!.action_required.map((item, index) => <tr key={index}>
            <td>{stateLabel(item.category)}</td>
            <td><a href={`/exceptions?case=${item.exception_id}`}><b>{item.description}</b></a></td>
            <td><span className={`state ${item.severity === "CRITICAL" ? "state-absent" : "state-pending"}`}>{item.severity}</span></td>
          </tr>)}</tbody>
        </table></div>
      </article>}

      <div className="twoCol">
        <article className="panel">
          <h2>Status pekerja</h2>
          <p>Roster status ringkas semua pekerja hari ini.</p>
          <div className="tableWrap"><table>
            <thead><tr><th>Pekerja</th><th>Peran · Crew</th><th>Status kerja</th><th>Operasional</th></tr></thead>
            <tbody>{snap!.roster_status.map(row => <tr key={row.employee_name}>
              <td><b>{row.employee_name}</b></td>
              <td>{row.role_name}<small>{row.crew_name}</small></td>
              <td>{row.work_status}</td>
              <td><span className={`state ${stateClass(row.operational_state)}`}>{stateLabel(row.operational_state)}</span></td>
            </tr>)}</tbody>
          </table></div>
        </article>

        <div>
          <article className="panel">
            <h2>Status alat (plan vs actual)</h2>
            <p>Penugasan unit dibanding realisasi operator.</p>
            <div className="tableWrap"><table>
              <thead><tr><th>Operator</th><th>Rencana → Aktual</th><th>Hasil</th></tr></thead>
              <tbody>{snap!.equipment_status.map(row => <tr key={row.employee_name}>
                <td><b>{row.employee_name}</b></td>
                <td>{row.plan_display}<small>Aktual: {row.actual_display || "—"}</small></td>
                <td><span className={`state ${row.comparison_result === "MATCH" ? "state-present" : "state-absent"}`}>{stateLabel(row.comparison_result)}</span></td>
              </tr>)}</tbody>
            </table></div>
          </article>

          <article className="panel">
            <h2>Exception aktif</h2>
            <p>{snap!.active_exceptions.length} kasus memerlukan penanganan.</p>
            {snap!.active_exceptions.map(exc => <p key={exc.exception_id}>
              <a href={`/exceptions?case=${exc.exception_id}`}>
                <b>{exc.exception_type}</b> — {exc.employee_name}
              </a><br/>
              <small>{exc.status} · {exc.severity}</small>
            </p>)}
            {!snap!.active_exceptions.length && <p>Semua aman — tidak ada exception aktif.</p>}
          </article>
        </div>
      </div>
    </>}
  </Shell>;
}
