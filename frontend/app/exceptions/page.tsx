"use client";
import {useEffect, useState} from "react";
import Shell, {ErrorBox} from "../metro-shell";
import {apiFetch, stateClass, stateLabel, witaDateTime} from "../metro-api";

type ExceptionItem = {
  case_id: string; exception_type: string; severity: string; status: string;
  employee_name: string; employee_code: string; role_name: string; crew_name: string;
  operating_date: string; shift_name: string; detected_at: string;
  owner_name: string; source_type: string;
};
type ExceptionDetail = {
  summary: ExceptionItem & {
    acknowledged_at: string | null; resolved_at: string | null;
    rule_version_name: string | null; current_owner_id: string;
  };
  plan_vs_actual: Record<string, unknown> | null;
  decisions: Record<string, unknown>[];
  evidence: Record<string, unknown>[];
  actions: Record<string, unknown>[];
};

export default function Exceptions() {
  const [items, setItems] = useState<ExceptionItem[]>([]);
  const [selected, setSelected] = useState<ExceptionDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    apiFetch("/exceptions?active_only=false")
      .then(body => {
        setItems(body.data.items);
        // Deep-link support: /exceptions?case=<case_id>
        const caseId = new URLSearchParams(window.location.search).get("case");
        const target = body.data.items.find((item: ExceptionItem) => item.case_id === caseId) ?? body.data.items[0];
        if (target) return apiFetch(`/exceptions/${target.case_id}`).then(body => setSelected(body.data));
      })
      .catch(err => setError(err.message))
      .finally(() => setBusy(false));
  }, []);

  async function openCase(caseId: string) {
    setSelected(null);
    setError("");
    try {
      const body = await apiFetch(`/exceptions/${caseId}`);
      setSelected(body.data);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return <Shell active="/exceptions" eyebrow="Metro Mining · Workbench" title="Exception Cases"
      subtitle="Kasus kehadiran & penugasan yang memerlukan penanganan supervisor."
      badge={items.length > 0 && <span className="phase">{items.length} kasus</span>}>
    {error && <ErrorBox message={error}/>}
    {!busy && !error && <div className="twoCol">
      <article className="panel">
        <h2>Daftar kasus</h2>
        <div className="tableWrap"><table>
          <thead><tr><th>Kasus</th><th>Tipe</th><th>Status</th></tr></thead>
          <tbody>{items.map(item => <tr key={item.case_id} onClick={() => openCase(item.case_id)}
              className={selected?.summary.case_id === item.case_id ? "rowActive" : ""}>
            <td><b>{item.employee_name}</b><small>{item.shift_name} · {witaDateTime(item.detected_at)}</small></td>
            <td>{item.exception_type}<small>{item.severity}</small></td>
            <td><span className={`state ${stateClass(item.status, "exc")}`}>{stateLabel(item.status)}</span></td>
          </tr>)}</tbody>
        </table></div>
      </article>

      <article className="panel">
        {!selected && <p className="muted">Pilih kasus untuk melihat detail.</p>}
        {selected && <>
          <h2>{selected.summary.exception_type}</h2>
          <p>{selected.summary.employee_name} ({selected.summary.employee_code}) · {selected.summary.role_name} · {selected.summary.crew_name}</p>
          <span className={`state ${stateClass(selected.summary.status, "exc")}`}>
            {stateLabel(selected.summary.status)} · {selected.summary.severity}
          </span>
          <dl className="detailList">
            <div><dt>Tanggal operasi</dt><dd>{selected.summary.operating_date} · {selected.summary.shift_name}</dd></div>
            <div><dt>Terdeteksi</dt><dd>{witaDateTime(selected.summary.detected_at)} WITA</dd></div>
            <div><dt>Pemilik kasus</dt><dd>{selected.summary.owner_name || selected.summary.current_owner_id}</dd></div>
            <div><dt>Sumber</dt><dd>{selected.summary.source_type}</dd></div>
            {selected.summary.acknowledged_at && <div><dt>Ditanggapi</dt><dd>{witaDateTime(selected.summary.acknowledged_at)} WITA</dd></div>}
            {selected.summary.resolved_at && <div><dt>Selesai</dt><dd>{witaDateTime(selected.summary.resolved_at)} WITA</dd></div>}
            {selected.summary.rule_version_name && <div><dt>Versi aturan</dt><dd>{selected.summary.rule_version_name}</dd></div>}
          </dl>
          {selected.plan_vs_actual && <div className="proof review"><strong>Plan vs actual</strong>
            <pre className="jsonBlock">{JSON.stringify(selected.plan_vs_actual, null, 1)}</pre>
          </div>}
          {selected.decisions.length > 0 && <p>Keputusan tertunda: {selected.decisions.length}</p>}
          <p className="footnote">Aksi lanjutan (acknowledge/resolve/waive/assign) tersedia via API workbench.</p>
        </>}
      </article>
    </div>}
  </Shell>;
}
