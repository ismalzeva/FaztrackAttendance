"use client";
import {useEffect, useState, useCallback} from "react";
import {getApiBase} from "../metro-api";

/* ── Types ──────────────────────────────────────────────────────────── */
type Phase = "login" | "loading" | "ready" | "working" | "result";
type Project = {id: string; code: string; name: string; latitude: number; longitude: number; radius_m: number};
type ShiftInfo = {
  work_date: string; timezone: string; scheduled: boolean; allow_multi_checkin: boolean;
  schedule: {start: string | null; end: string | null};
  projects: Project[];
  open_shift: {project_id: string; project_name: string; since: string} | null;
  timeline: {event_type: string; project_name: string; server_time: string; status: string; distance_m: number}[];
};
type EventResult = {
  event_id: string; status: string; reason_code: string | null;
  distance_m: number; project_name: string; server_time: string;
};

const STATUS_STYLES: Record<string, {bg: string; icon: string; label: string}> = {
  VALID:    {bg: "#E6FFFA", icon: "✓", label: "Absen Tervalidasi"},
  REVIEW:   {bg: "#FFFBEB", icon: "⏳", label: "Perlu Verifikasi"},
  REJECTED: {bg: "#FFF5F5", icon: "✕", label: "Absen Ditolak"},
};
const REASON_LABELS: Record<string, string> = {
  GEOFENCE_UNCERTAIN: "Lokasi ragu-ragu — supervisor akan memverifikasi",
  OUTSIDE_GEOFENCE: "Anda berada di luar area kerja",
  GPS_ACCURACY_TOO_LOW: "Sinyal GPS kurang akurat — coba di tempat terbuka",
  NOT_SCHEDULED_TODAY: "Anda tidak memiliki jadwal kerja hari ini",
};

/* ── Component ──────────────────────────────────────────────────────── */
export default function Absen() {
  const [phase, setPhase] = useState<Phase>("login");
  const [tenantCode, setTenantCode] = useState("metro-mining");
  const [workerCode, setWorkerCode] = useState("");
  const [pin, setPin] = useState("");
  const [workerToken, setWorkerToken] = useState("");
  const [workerName, setWorkerName] = useState("");
  const [shift, setShift] = useState<ShiftInfo | null>(null);
  const [selectedProject, setSelectedProject] = useState<string>("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<EventResult | null>(null);

  const api = getApiBase();
  const authH = (tok: string) => ({Authorization: `Bearer ${tok}`, "Content-Type": "application/json"});

  /* ── Restore session ── */
  useEffect(() => {
    const tok = localStorage.getItem("faztrack_wt");
    const tc = localStorage.getItem("faztrack_tc") || "metro-mining";
    const wc = localStorage.getItem("faztrack_wc");
    const wn = localStorage.getItem("faztrack_wn");
    if (tok && wc) {
      setWorkerToken(tok); setTenantCode(tc); setWorkerCode(wc); setWorkerName(wn ?? "");
      loadShift(tok);
    }
  }, []);

  /* ── Load shift info ── */
  const loadShift = useCallback(async (tok: string) => {
    setPhase("loading");
    try {
      const r = await fetch(`${api}/worker-web/shift`, {headers: authH(tok)});
      if (!r.ok) { logout(); return; }
      const d = await r.json();
      setShift(d.data);
      if (d.data.projects.length === 1) setSelectedProject(d.data.projects[0].id);
      setPhase("ready");
    } catch { logout(); }
  }, [api]);

  /* ── Login ── */
  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setMessage("");
    try {
      const r = await fetch(`${api}/worker-web/login`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({tenant_code: tenantCode, worker_code: workerCode, pin}),
      });
      const b = await r.json();
      if (!r.ok) throw new Error(b.detail?.code === "INVALID_CREDENTIALS" ? "ID pekerja atau PIN salah" : "Login gagal");
      const tok = b.data.access_token;
      const wn = b.data.worker?.name ?? "";
      localStorage.setItem("faztrack_wt", tok);
      localStorage.setItem("faztrack_tc", tenantCode);
      localStorage.setItem("faztrack_wc", workerCode);
      localStorage.setItem("faztrack_wn", wn);
      setWorkerToken(tok); setWorkerName(wn);
      await loadShift(tok);
    } catch (err) { setMessage(err instanceof Error ? err.message : "Login gagal"); }
    finally { setBusy(false); }
  }

  /* ── GPS ── */
  function getGPS(): Promise<{lat: number; lon: number; acc: number}> {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) return reject(new Error("Perangkat tidak mendukung GPS"));
      navigator.geolocation.getCurrentPosition(
        p => resolve({lat: p.coords.latitude, lon: p.coords.longitude, acc: p.coords.accuracy}),
        err => reject(new Error(
          err.code === 1 ? "Izin lokasi ditolak — aktifkan di pengaturan browser" :
          err.code === 2 ? "Sinyal GPS tidak tersedia — coba di luar ruangan" :
          "Gagal mengambil lokasi — coba lagi"
        )),
        {enableHighAccuracy: true, timeout: 15000, maximumAge: 0},
      );
    });
  }

  /* ── Attendance flow ── */
  async function doAttendance(eventType: "CHECK_IN" | "CHECK_OUT") {
    if (eventType === "CHECK_IN" && !selectedProject) { setMessage("Pilih lokasi kerja terlebih dahulu"); return; }
    setBusy(true); setMessage(""); setResult(null);
    try {
      const gps = await getGPS();

      // Challenge
      const chR = await fetch(`${api}/worker-web/challenge`, {
        method: "POST", headers: authH(workerToken),
        body: JSON.stringify({event_type: eventType, project_id: selectedProject || shift?.open_shift?.project_id}),
      });
      const chB = await chR.json();
      if (!chR.ok) {
        const code = chB.detail?.code;
        throw new Error(REASON_LABELS[code] ?? code ?? "Gagal meminta tantangan");
      }
      const ch = chB.data;

      // Submit
      const subR = await fetch(`${api}/worker-web/events`, {
        method: "POST", headers: authH(workerToken),
        body: JSON.stringify({
          challenge_id: ch.challenge_id, challenge: ch.challenge,
          event_type: eventType, project_id: ch.project.id,
          latitude: gps.lat, longitude: gps.lon, accuracy_m: gps.acc,
          captured_at_client: new Date().toISOString(),
        }),
      });
      const subB = await subR.json();
      if (!subR.ok) throw new Error(subB.detail?.code ?? "Gagal mengirim absensi");
      setResult(subB.data);
      setPhase("result");
      loadShift(workerToken);
    } catch (err) { setMessage(err instanceof Error ? err.message : "Terjadi kesalahan"); }
    finally { setBusy(false); }
  }

  /* ── Logout ── */
  function logout() {
    ["faztrack_wt", "faztrack_tc", "faztrack_wc", "faztrack_wn"].forEach(k => localStorage.removeItem(k));
    setWorkerToken(""); setShift(null); setResult(null); setPhase("login"); setMessage("");
  }

  /* ── Render ── */
  return (
    <main style={{
      minHeight: "100dvh",
      background: "#102A43",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      padding: "20px 16px",
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
    }}>
      {/* Brand header */}
      <div style={{
        textAlign: "center",
        marginBottom: 24,
        color: "white",
      }}>
        <div style={{
          fontSize: 22,
          fontWeight: 800,
          letterSpacing: "-0.02em",
          marginBottom: 4,
        }}>
          FAZTRACK<span style={{color: "#F5A623"}}>.</span>
        </div>
        <div style={{
          fontSize: 11,
          fontWeight: 600,
          color: "#9FB3C8",
          textTransform: "uppercase",
          letterSpacing: "0.1em",
        }}>
          Attendance System
        </div>
      </div>

      {/* Card */}
      <section style={{
        background: "white",
        width: "100%",
        maxWidth: 400,
        borderRadius: 16,
        overflow: "hidden",
        boxShadow: "0 4px 24px rgba(0,0,0,0.2)",
      }}>
        {/* Amber accent bar */}
        <div style={{height: 4, background: "#F5A623"}} />

        <div style={{padding: "28px 24px 24px"}}>

          {/* ── LOGIN ── */}
          {phase === "login" && <>
            <div style={{textAlign: "center", marginBottom: 24}}>
              <div style={{
                fontSize: 13,
                fontWeight: 700,
                color: "#D98200",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                marginBottom: 8,
              }}>
                Metro Mining
              </div>
              <h1 style={{
                fontSize: 22,
                fontWeight: 800,
                color: "#102A43",
                margin: 0,
                lineHeight: 1.3,
              }}>
                Absensi Karyawan
              </h1>
              <p style={{
                fontSize: 14,
                color: "#52606D",
                margin: "8px 0 0",
                lineHeight: 1.5,
              }}>
                Masukkan ID pekerja dan PIN Anda.
              </p>
            </div>

            <form onSubmit={handleLogin}>
              <label style={{display: "block", marginBottom: 16}}>
                <span style={{
                  display: "block",
                  fontSize: 12,
                  fontWeight: 700,
                  color: "#52606D",
                  marginBottom: 6,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}>ID Pekerja</span>
                <input
                  value={workerCode}
                  onChange={e => setWorkerCode(e.target.value)}
                  placeholder="contoh: W001"
                  autoComplete="username"
                  style={{
                    width: "100%",
                    border: "1.5px solid #D9E2EC",
                    borderRadius: 10,
                    padding: "14px 16px",
                    fontSize: 16,
                    fontFamily: "inherit",
                    outline: "none",
                    transition: "border-color 0.15s",
                  }}
                  onFocus={e => e.target.style.borderColor = "#F5A623"}
                  onBlur={e => e.target.style.borderColor = "#D9E2EC"}
                />
              </label>

              <label style={{display: "block", marginBottom: 20}}>
                <span style={{
                  display: "block",
                  fontSize: 12,
                  fontWeight: 700,
                  color: "#52606D",
                  marginBottom: 6,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}>PIN</span>
                <input
                  type="password"
                  inputMode="numeric"
                  value={pin}
                  onChange={e => setPin(e.target.value)}
                  placeholder="••••"
                  autoComplete="current-password"
                  style={{
                    width: "100%",
                    border: "1.5px solid #D9E2EC",
                    borderRadius: 10,
                    padding: "14px 16px",
                    fontSize: 16,
                    fontFamily: "inherit",
                    outline: "none",
                    transition: "border-color 0.15s",
                  }}
                  onFocus={e => e.target.style.borderColor = "#F5A623"}
                  onBlur={e => e.target.style.borderColor = "#D9E2EC"}
                />
              </label>

              <button
                type="submit"
                disabled={busy || !workerCode || !pin}
                style={{
                  width: "100%",
                  padding: "16px 0",
                  fontSize: 16,
                  fontWeight: 800,
                  fontFamily: "inherit",
                  background: "#F5A623",
                  color: "#102A43",
                  border: 0,
                  borderRadius: 10,
                  cursor: busy ? "wait" : "pointer",
                  opacity: (busy || !workerCode || !pin) ? 0.55 : 1,
                  transition: "opacity 0.15s, background 0.15s",
                }}
              >
                {busy ? "Memeriksa…" : "Masuk"}
              </button>
            </form>

            <input type="hidden" value={tenantCode} />
          </>}

          {/* ── LOADING ── */}
          {phase === "loading" && (
            <div style={{textAlign: "center", padding: "48px 0", color: "#52606D"}}>
              <div style={{
                width: 32, height: 32,
                border: "3px solid #D9E2EC",
                borderTopColor: "#F5A623",
                borderRadius: "50%",
                margin: "0 auto 16px",
                animation: "spin 0.8s linear infinite",
              }} />
              <p style={{fontSize: 15, fontWeight: 600}}>Memuat jadwal…</p>
            </div>
          )}

          {/* ── READY ── */}
          {phase === "ready" && shift && <>
            {/* Worker info */}
            <div style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 20,
              paddingBottom: 16,
              borderBottom: "1px solid #D9E2EC",
            }}>
              <div>
                <div style={{fontSize: 18, fontWeight: 800, color: "#102A43"}}>
                  {workerName || workerCode}
                </div>
                <div style={{fontSize: 13, color: "#52606D", marginTop: 2}}>
                  {shift.work_date} · {shift.timezone}
                </div>
              </div>
              <button
                onClick={logout}
                style={{
                  background: "none",
                  border: "1px solid #D9E2EC",
                  borderRadius: 8,
                  padding: "8px 12px",
                  fontSize: 12,
                  fontWeight: 700,
                  color: "#52606D",
                  cursor: "pointer",
                }}
              >
                Keluar
              </button>
            </div>

            {/* Schedule info */}
            {shift.scheduled ? (
              <div style={{
                background: "#F0FFF4",
                padding: "12px 14px",
                borderRadius: 10,
                fontSize: 14,
                display: "flex",
                alignItems: "center",
                gap: 10,
                marginBottom: 16,
              }}>
                <span style={{fontSize: 18}}>🕐</span>
                <div>
                  <div style={{fontWeight: 700, color: "#102A43"}}>Jadwal Hari Ini</div>
                  <div style={{color: "#52606D", fontSize: 13}}>
                    {shift.schedule.start?.slice(0,5)} – {shift.schedule.end?.slice(0,5)} WITA
                  </div>
                </div>
              </div>
            ) : (
              <div style={{
                background: "#FFF5F5",
                padding: "12px 14px",
                borderRadius: 10,
                fontSize: 14,
                display: "flex",
                alignItems: "center",
                gap: 10,
                marginBottom: 16,
              }}>
                <span style={{fontSize: 18}}>⚠️</span>
                <div style={{fontWeight: 600, color: "#C53030"}}>Tidak ada jadwal hari ini</div>
              </div>
            )}

            {/* Open shift indicator */}
            {shift.open_shift && (
              <div style={{
                background: "#FFFBEB",
                padding: "12px 14px",
                borderRadius: 10,
                fontSize: 14,
                display: "flex",
                alignItems: "center",
                gap: 10,
                marginBottom: 16,
              }}>
                <span style={{fontSize: 18}}>📍</span>
                <div>
                  <div style={{fontWeight: 700, color: "#102A43"}}>Sedang Bekerja</div>
                  <div style={{color: "#52606D", fontSize: 13}}>
                    {shift.open_shift.project_name} sejak{" "}
                    {new Date(shift.open_shift.since).toLocaleTimeString("id-ID", {hour: "2-digit", minute: "2-digit"})}
                  </div>
                </div>
              </div>
            )}

            {/* Project selector */}
            {!shift.open_shift && shift.projects.length > 1 && (
              <label style={{display: "block", marginBottom: 16}}>
                <span style={{
                  display: "block",
                  fontSize: 12,
                  fontWeight: 700,
                  color: "#52606D",
                  marginBottom: 6,
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                }}>Lokasi Kerja</span>
                <select
                  value={selectedProject}
                  onChange={e => setSelectedProject(e.target.value)}
                  style={{
                    width: "100%",
                    border: "1.5px solid #D9E2EC",
                    borderRadius: 10,
                    padding: "14px 16px",
                    fontSize: 15,
                    fontFamily: "inherit",
                    background: "white",
                    color: "#102A43",
                    outline: "none",
                    appearance: "none",
                    backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M6 8L1 3h10z' fill='%2352606D'/%3E%3C/svg%3E")`,
                    backgroundRepeat: "no-repeat",
                    backgroundPosition: "right 16px center",
                  }}
                >
                  <option value="">— pilih lokasi —</option>
                  {shift.projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
            )}

            {/* Action buttons */}
            <div style={{display: "grid", gap: 12, marginTop: 8}}>
              {!shift.open_shift ? (
                <button
                  disabled={busy || !shift.scheduled || (shift.projects.length > 1 && !selectedProject)}
                  onClick={() => doAttendance("CHECK_IN")}
                  style={{
                    width: "100%",
                    padding: "18px 0",
                    fontSize: 17,
                    fontWeight: 800,
                    fontFamily: "inherit",
                    background: "#F5A623",
                    color: "#102A43",
                    border: 0,
                    borderRadius: 12,
                    cursor: busy ? "wait" : "pointer",
                    opacity: (busy || !shift.scheduled || (shift.projects.length > 1 && !selectedProject)) ? 0.55 : 1,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 10,
                  }}
                >
                  {busy ? (
                    <>
                      <span style={{
                        width: 18, height: 18,
                        border: "2px solid #102A4340",
                        borderTopColor: "#102A43",
                        borderRadius: "50%",
                        animation: "spin 0.8s linear infinite",
                      }} />
                      Mengambil lokasi…
                    </>
                  ) : (
                    <>
                      <svg width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z"/><circle cx="12" cy="10" r="3"/></svg>
                      CHECK IN
                    </>
                  )}
                </button>
              ) : (
                <button
                  disabled={busy}
                  onClick={() => doAttendance("CHECK_OUT")}
                  style={{
                    width: "100%",
                    padding: "18px 0",
                    fontSize: 17,
                    fontWeight: 800,
                    fontFamily: "inherit",
                    background: "#102A43",
                    color: "white",
                    border: 0,
                    borderRadius: 12,
                    cursor: busy ? "wait" : "pointer",
                    opacity: busy ? 0.55 : 1,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 10,
                  }}
                >
                  {busy ? (
                    <>
                      <span style={{
                        width: 18, height: 18,
                        border: "2px solid #ffffff40",
                        borderTopColor: "white",
                        borderRadius: "50%",
                        animation: "spin 0.8s linear infinite",
                      }} />
                      Mengambil lokasi…
                    </>
                  ) : (
                    <>
                      <svg width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
                      CHECK OUT
                    </>
                  )}
                </button>
              )}
            </div>

            {/* Timeline */}
            {shift.timeline.length > 0 && (
              <div style={{marginTop: 24, borderTop: "1px solid #D9E2EC", paddingTop: 16}}>
                <div style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: "#52606D",
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                  marginBottom: 10,
                }}>
                  Riwayat Hari Ini
                </div>
                {shift.timeline.map((t, i) => (
                  <div key={i} style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    fontSize: 14,
                    padding: "10px 0",
                    borderBottom: i < shift.timeline.length - 1 ? "1px solid #f0f0f0" : "none",
                  }}>
                    <div style={{display: "flex", alignItems: "center", gap: 10}}>
                      <span style={{
                        width: 8, height: 8,
                        borderRadius: "50%",
                        background: t.event_type === "CHECK_IN" ? "#2F855A" : "#C53030",
                      }} />
                      <span style={{fontWeight: 600, color: "#102A43"}}>
                        {t.event_type === "CHECK_IN" ? "Masuk" : "Keluar"}
                      </span>
                      <span style={{color: "#52606D", fontSize: 13}}>{t.project_name}</span>
                    </div>
                    <div style={{textAlign: "right"}}>
                      <div style={{fontWeight: 600, color: "#102A43"}}>
                        {new Date(t.server_time).toLocaleTimeString("id-ID", {hour: "2-digit", minute: "2-digit"})}
                      </div>
                      <div style={{fontSize: 11, color: "#9FB3C8"}}>{Math.round(t.distance_m)}m</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>}

          {/* ── RESULT ── */}
          {phase === "result" && result && (() => {
            const s = STATUS_STYLES[result.status] ?? STATUS_STYLES.REJECTED;
            return <>
              <div style={{
                textAlign: "center",
                background: s.bg,
                margin: "-28px -24px 24px",
                padding: "40px 24px 32px",
              }}>
                <div style={{
                  width: 56, height: 56,
                  borderRadius: "50%",
                  background: result.status === "VALID" ? "#2F855A" : result.status === "REVIEW" ? "#D98200" : "#C53030",
                  color: "white",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 28,
                  fontWeight: 800,
                  margin: "0 auto 12px",
                }}>
                  {s.icon}
                </div>
                <h1 style={{margin: 0, fontSize: 20, fontWeight: 800, color: "#102A43"}}>{s.label}</h1>
                <p style={{color: "#52606D", fontSize: 14, margin: "6px 0 0"}}>{result.project_name}</p>
              </div>

              <div style={{display: "grid", gap: 12, fontSize: 14}}>
                <div style={{
                  display: "flex",
                  justifyContent: "space-between",
                  padding: "10px 0",
                  borderBottom: "1px solid #D9E2EC",
                }}>
                  <span style={{color: "#52606D"}}>Jarak dari titik</span>
                  <strong style={{color: "#102A43"}}>
                    {result.distance_m < 1 ? "< 1" : Math.round(result.distance_m)} meter
                  </strong>
                </div>
                {result.reason_code && (
                  <p style={{
                    color: result.status === "VALID" ? "#2F855A" : "#C53030",
                    fontWeight: 600,
                    margin: 0,
                    padding: "10px 12px",
                    background: result.status === "VALID" ? "#F0FFF4" : "#FFF5F5",
                    borderRadius: 8,
                    fontSize: 13,
                  }}>
                    {REASON_LABELS[result.reason_code] ?? result.reason_code}
                  </p>
                )}
              </div>

              <button
                onClick={() => {setPhase("ready"); setResult(null);}}
                style={{
                  width: "100%",
                  marginTop: 24,
                  padding: "14px 0",
                  fontSize: 15,
                  fontWeight: 700,
                  fontFamily: "inherit",
                  background: "#F5A623",
                  color: "#102A43",
                  border: 0,
                  borderRadius: 10,
                  cursor: "pointer",
                }}
              >
                Kembali
              </button>
            </>;
          })()}

          {/* ── ERROR ── */}
          {message && (
            <div style={{
              color: "#C53030",
              background: "#FFF5F5",
              padding: "12px 14px",
              borderRadius: 10,
              marginTop: 16,
              fontSize: 14,
              fontWeight: 500,
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}>
              <span style={{fontSize: 16}}>⚠</span>
              {message}
            </div>
          )}

          {/* Footer */}
          <div style={{
            marginTop: 20,
            paddingTop: 16,
            borderTop: "1px solid #D9E2EC",
            textAlign: "center",
          }}>
            <p style={{
              fontSize: 11,
              color: "#9FB3C8",
              lineHeight: 1.6,
              margin: 0,
            }}>
              Sistem GPS · Izin lokasi harus aktif
            </p>
          </div>
        </div>
      </section>

      {/* CSS Animation */}
      <style jsx global>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        @media (max-width: 480px) {
          input, select {
            font-size: 16px !important;
          }
        }
      `}</style>
    </main>
  );
}
