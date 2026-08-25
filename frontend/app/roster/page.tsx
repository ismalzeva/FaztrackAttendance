"use client";
import {useEffect, useState} from "react";
import Shell, {ErrorBox} from "../metro-shell";
import {apiFetch, stateClass, stateLabel} from "../metro-api";

type RosterItem = {
  employee_id: string; employee_name: string; employee_code: string;
  role_name: string; crew_name: string;
  work_status: string; shift_name: string; site_status: string;
  planned_equipment_code: string | null; actual_equipment_code: string | null;
  operational_state: string; checkpoint_status_summary: string;
  active_exception_count: number; attention_badge: string | null;
};

const WORK_LABEL: Record<string, string> = {
  WORK: "Kerja", REST: "Istirahat", OFFSITE: "Offsite", LEAVE: "Cuti", OTHER: "Lainnya",
};

export default function Roster() {
  const [items, setItems] = useState<RosterItem[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  useEffect(() => {
    apiFetch("/roster/operational?operating_date=2026-09-01")
      .then(body => setItems(body.data.items))
      .catch(err => setError(err.message))
      .finally(() => setBusy(false));
  }, []);

  return <Shell active="/roster" eyebrow="Metro Mining · Roster" title="Roster Operasional"
      subtitle="Penugasan 12 pekerja untuk tanggal operasi demo (01 Sep 2026), Shift Siang & Shift Malam."
      badge={items.length > 0 && <span className="phase">{items.length} pekerja</span>}>
    {error && <ErrorBox message={error}/>}
    {!busy && !error &&
      <article className="panel">
        <div className="tableWrap"><table>
          <thead><tr>
            <th>Pekerja</th><th>Shift</th><th>Peran</th><th>Crew</th><th>Unit ditugaskan</th>
            <th>Status kerja</th><th>Status operasional</th><th>Checkpoint</th><th>Exception</th>
          </tr></thead>
          <tbody>{items.map(item => <tr key={item.employee_id}>
            <td><b>{item.employee_name}</b><small>{item.employee_code}</small></td>
            <td>{item.shift_name}</td>
            <td>{item.role_name}</td>
            <td>{item.crew_name}</td>
            <td>{item.planned_equipment_code ?? "—"}{item.actual_equipment_code && item.actual_equipment_code !== item.planned_equipment_code &&
              <small>Aktual: {item.actual_equipment_code}</small>}</td>
            <td>{WORK_LABEL[item.work_status] ?? item.work_status}</td>
            <td><span className={`state ${stateClass(item.operational_state)}`}>{stateLabel(item.operational_state)}</span></td>
            <td>{item.checkpoint_status_summary || "—"}</td>
            <td>{item.active_exception_count > 0
              ? <span className="state state-absent">{item.active_exception_count} kasus</span> : "—"}</td>
          </tr>)}</tbody>
        </table></div>
      </article>}
    {!busy && !error && <p className="footnote">Sumber: GET /api/v1/roster/operational?operating_date=2026-09-01 (WITA).</p>}
  </Shell>;
}
