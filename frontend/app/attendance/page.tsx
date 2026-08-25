"use client";
import {useEffect, useState} from "react";
import Shell, {ErrorBox} from "../metro-shell";
import {apiFetch, stateClass, stateLabel, witaTime} from "../metro-api";

type ReportRow = {
  employee_no: string; employee_name: string; role_name: string; crew_name: string;
  work_status: string; planned_equipment: string | null;
  briefing_in: string | null; equipment_in: string | null; work_start: string | null;
  break_out: string | null; break_in: string | null; handover: string | null;
  shift_out: string | null; operational_state: string;
  attendance_exception_count: number; exception_types: string;
};
type TimelineEvent = {
  timestamp: string; event_type: string; site_name: string | null;
  equipment_code: string | null; evidence_available: boolean;
};

const COLUMNS: {key: keyof ReportRow; label: string}[] = [
  {key: "briefing_in", label: "Briefing in"},
  {key: "equipment_in", label: "Unit masuk"},
  {key: "work_start", label: "Mulai kerja"},
  {key: "break_out", label: "Istirahat keluar"},
  {key: "break_in", label: "Istirahat masuk"},
  {key: "handover", label: "Serah terima"},
  {key: "shift_out", label: "Pulang shift"},
];

export default function Attendance() {
  const [rows, setRows] = useState<ReportRow[]>([]);
  const [timelines, setTimelines] = useState<Record<string, TimelineEvent[]>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const report = await apiFetch("/reports/shift-attendance?operating_date=2026-09-01");
        if (!alive) return;
        const reportRows: ReportRow[] = report.rows;
        setRows(reportRows);
        // Canonical event timeline per worker (checkpoint view).
        const entries = await Promise.all(reportRows.map(async row => {
          const workerId = row.employee_no.replace(/^em-/, "");
          try {
            const body = await apiFetch(`/roster/operational/${workerId}/timeline?operating_date=2026-09-01`);
            return [workerId, (body.data ?? []) as TimelineEvent[]] as const;
          } catch {
            return [workerId, [] as TimelineEvent[]] as const;
          }
        }));
        if (!alive) return;
        setTimelines(Object.fromEntries(entries));
      } catch (err) {
        if (alive) setError((err as Error).message);
      } finally {
        if (alive) setBusy(false);
      }
    })();
    return () => {alive = false;};
  }, []);

  return <Shell active="/attendance" eyebrow="Metro Mining · Kehadiran" title="Absensi & Checkpoint"
      subtitle="Peristiwa absensi kanonik per pekerja untuk tanggal operasi demo (01 Sep 2026), waktu WITA."
      badge={rows.length > 0 && <span className="phase">{rows.length} pekerja</span>}>
    {error && <ErrorBox message={error}/>}
    {!busy && !error && <>
      <article className="panel">
        <h2>Garis waktu checkpoint</h2>
        <p>Check-in / check-out kanonik dari perangkat pekerja (sumber kebenaran absensi).</p>
        <div className="timelineGrid">
          {Object.entries(timelines).map(([workerId, events]) => {
            const row = rows.find(item => item.employee_no === `em-${workerId}`);
            return <div className="timelineCard" key={workerId}>
              <b>{row?.employee_name ?? workerId}</b>
              <small>{row ? `${row.role_name} · ${row.crew_name}` : ""}</small>
              {events.length === 0 && <p className="muted">Belum ada peristiwa absensi.</p>}
              {events.map((event, index) => <div className="eventRow" key={index}>
                <span className={`state ${event.event_type === "CHECK_IN" ? "state-present" : "state-pending"}`}>
                  {event.event_type === "CHECK_IN" ? "Check-in" : "Check-out"}
                </span>
                <b>{witaTime(event.timestamp)}</b>
                <small>{event.site_name ?? ""}{event.equipment_code ? ` · ${event.equipment_code}` : ""}</small>
              </div>)}
            </div>;
          }).sort((a, b) => 0)}
        </div>
      </article>

      <article className="panel">
        <h2>Rekap shift attendance</h2>
        <p>Laporan resmi per shift — kolom checkpoint mengikuti alur briefing → pulang shift.</p>
        <div className="tableWrap"><table>
          <thead><tr>
            <th>Pekerja</th><th>Shift</th><th>Status kerja</th><th>Unit rencana</th>
            {COLUMNS.map(col => <th key={col.key}>{col.label}</th>)}
            <th>Status operasional</th><th>Exception</th>
          </tr></thead>
          <tbody>{rows.map(row => <tr key={row.employee_no}>
            <td><b>{row.employee_name}</b><small>{row.role_name}</small></td>
            <td>{"—"}<small>{row.crew_name}</small></td>
            <td>{row.work_status}</td>
            <td>{row.planned_equipment ?? "—"}</td>
            {COLUMNS.map(col => <td key={String(col.key)}>{witaTime(row[col.key] as string | null)}</td>)}
            <td><span className={`state ${stateClass(row.operational_state)}`}>{stateLabel(row.operational_state)}</span></td>
            <td>{row.attendance_exception_count > 0
              ? <span className="state state-absent" title={row.exception_types}>{row.exception_types || `${row.attendance_exception_count} kasus`}</span>
              : "—"}</td>
          </tr>)}</tbody>
        </table></div>
      </article>

      <p className="footnote">Sumber: GET /api/v1/reports/shift-attendance + GET /api/v1/roster/operational/&#123;worker&#125;/timeline.</p>
    </>}
  </Shell>;
}
