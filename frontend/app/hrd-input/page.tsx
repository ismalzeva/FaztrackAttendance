"use client";
import { useState, useRef, useEffect } from "react";

const API = "/api/v1/hrd";

interface ColumnMapping {
  name: string;
  maps_to: string;
}

interface ShiftRule {
  id: string;
  name: string;
  description: string;
  status: string;
  rules: {
    shifts: { name: string; start_time: string; end_time: string; break_start?: string; break_end?: string }[];
    rotation: { type: string; params: Record<string, number> }[];
    constraints: { type: string; params: Record<string, number> }[];
  };
  natural_language_input?: string;
  ai_confidence?: number;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export default function HRDInputPage() {
  const [step, setStep] = useState<"upload" | "mapping" | "rules" | "preview" | "import" | "done">("upload");
  const [token, setToken] = useState<string>("");
  const [importId, setImportId] = useState<string>("");
  const [filename, setFilename] = useState<string>("");
  const [headers, setHeaders] = useState<string[]>([]);
  const [autoMapping, setAutoMapping] = useState<ColumnMapping[]>([]);
  const [preview, setPreview] = useState<Record<string, string>[]>([]);
  const [totalRows, setTotalRows] = useState(0);
  const [validRows, setValidRows] = useState(0);
  const [errorRows, setErrorRows] = useState(0);
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(false);

  // Shift rules
  const [shiftRules, setShiftRules] = useState<ShiftRule[]>([]);
  const [selectedRuleId, setSelectedRuleId] = useState<string>("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [parsedRuleset, setParsedRuleset] = useState<any>(null);

  // Import config
  const [projectId, setProjectId] = useState("");
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [scheduleDays, setScheduleDays] = useState(90);
  const [generateSchedule, setGenerateSchedule] = useState(true);
  const [importResult, setImportResult] = useState<any>(null);

  const fileRef = useRef<HTMLInputElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Auto-login for demo
  useEffect(() => {
    const saved = localStorage.getItem("faztrack_token");
    if (saved) {
      setToken(saved);
      loadProjects(saved);
      loadShiftRules(saved);
    }
  }, []);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  const apiFetch = async (url: string, opts: RequestInit = {}) => {
    const headers: Record<string, string> = {
      ...(opts.headers as Record<string, string> || {}),
    };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(url, { ...opts, headers });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  };

  const loadProjects = async (t: string) => {
    try {
      const data = await fetch("/api/v1/projects", {
        headers: { Authorization: `Bearer ${t}` },
      }).then(r => r.json());
      setProjects(data.map((p: any) => ({ id: p.id, name: p.name })));
    } catch {}
  };

  const loadShiftRules = async (t: string) => {
    try {
      const data = await fetch(`${API}/shift-rules`, {
        headers: { Authorization: `Bearer ${t}` },
      }).then(r => r.json());
      setShiftRules(data);
    } catch {}
  };

  const handleLogin = async () => {
    const email = prompt("Email:");
    const password = prompt("Password:");
    if (!email || !password) return;
    try {
      const res = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (data.access_token) {
        setToken(data.access_token);
        localStorage.setItem("faztrack_token", data.access_token);
        loadProjects(data.access_token);
        loadShiftRules(data.access_token);
      }
    } catch (e: any) {
      alert("Login gagal: " + e.message);
    }
  };

  // ── Step 1: Upload ──────────────────────────────────────────────
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadProgress(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const data = await apiFetch(`${API}/upload`, { method: "POST", body: form });
      setImportId(data.import_id);
      setFilename(data.filename);
      setHeaders(data.headers);
      setAutoMapping(data.auto_mapping);
      setPreview(data.preview);
      setTotalRows(data.total_rows);
      setStep("mapping");
    } catch (e: any) {
      alert("Upload gagal: " + e.message);
    } finally {
      setUploadProgress(false);
    }
  };

  // ── Step 2: Column Mapping ──────────────────────────────────────
  const updateMapping = (colName: string, mapsTo: string) => {
    setAutoMapping(prev =>
      prev.map(m => m.name === colName ? { ...m, maps_to: mapsTo } : m)
    );
  };

  const handlePreview = async () => {
    setLoading(true);
    try {
      const data = await apiFetch(`${API}/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          import_id: importId,
          column_mapping: autoMapping,
          shift_rule_id: selectedRuleId || undefined,
        }),
      });
      setValidRows(data.valid_rows);
      setErrors(data.errors);
      setErrorRows(data.errors.length);
      setPreview(data.preview);
      setStep("rules");
    } catch (e: any) {
      alert("Preview gagal: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  // ── Step 3: AI Chat for Shift Rules ─────────────────────────────
  const handleChat = async () => {
    if (!chatInput.trim()) return;
    const userMsg = chatInput.trim();
    setChatInput("");
    setChatMessages(prev => [...prev, { role: "user", content: userMsg }]);
    setChatLoading(true);
    try {
      const data = await apiFetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: userMsg,
          context: chatMessages.map(m => `${m.role}: ${m.content}`).join("\n"),
          shift_rule_id: selectedRuleId || undefined,
        }),
      });
      setChatMessages(prev => [...prev, { role: "assistant", content: data.reply }]);
      if (data.ruleset) {
        setParsedRuleset(data.ruleset);
      }
      // Reload shift rules
      loadShiftRules(token);
    } catch (e: any) {
      setChatMessages(prev => [...prev, { role: "assistant", content: `Error: ${e.message}` }]);
    } finally {
      setChatLoading(false);
    }
  };

  const applyParsedRules = () => {
    if (!parsedRuleset) return;
    // Find the latest rule
    loadShiftRules(token);
    setStep("preview");
  };

  // ── Step 4: Preview & Import ────────────────────────────────────
  const handleGenerateSchedule = async () => {
    if (!selectedRuleId || !projectId) {
      alert("Pilih shift rule dan project terlebih dahulu");
      return;
    }
    setLoading(true);
    try {
      const data = await apiFetch(`${API}/generate-schedule`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          import_id: importId,
          project_id: projectId,
          shift_rule_id: selectedRuleId,
          generate_schedule: generateSchedule,
          schedule_days: scheduleDays,
        }),
      });
      setPreview(data.schedule_preview);
      setTotalRows(data.total_entries);
      setStep("import");
    } catch (e: any) {
      alert("Generate gagal: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleConfirmImport = async () => {
    if (!projectId) {
      alert("Pilih project terlebih dahulu");
      return;
    }
    setLoading(true);
    try {
      const data = await apiFetch(`${API}/confirm-import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          import_id: importId,
          project_id: projectId,
          shift_rule_id: selectedRuleId || undefined,
          generate_schedule: generateSchedule,
          schedule_days: scheduleDays,
        }),
      });
      setImportResult(data);
      setStep("done");
    } catch (e: any) {
      alert("Import gagal: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  // ── Render ──────────────────────────────────────────────────────
  const TARGET_FIELDS = [
    { value: "ignore", label: "-- Abaikan --" },
    { value: "worker_name", label: "Nama Karyawan" },
    { value: "worker_code", label: "Kode/NIP" },
    { value: "position", label: "Jabatan" },
    { value: "phone", label: "No. HP" },
    { value: "shift", label: "Shift" },
    { value: "location", label: "Lokasi/Pos" },
    { value: "latitude", label: "Latitude" },
    { value: "longitude", label: "Longitude" },
  ];

  return (
    <div style={{ minHeight: "100vh", background: "#0B0B0B", color: "#E0E0E0", fontFamily: "Inter, system-ui, sans-serif" }}>
      {/* Header */}
      <div style={{ background: "#15171C", borderBottom: "1px solid #2A2D35", padding: "16px 24px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, background: "#FF7A00", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 18 }}>F</div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 16 }}>HRD Input System</div>
            <div style={{ fontSize: 12, color: "#888" }}>Faztrack Attendance — Bulk Import</div>
          </div>
        </div>
        {!token ? (
          <button onClick={handleLogin} style={{ background: "#FF7A00", color: "#fff", border: "none", borderRadius: 8, padding: "8px 16px", cursor: "pointer", fontWeight: 600 }}>
            Login
          </button>
        ) : (
          <div style={{ fontSize: 12, color: "#888" }}>✓ Terotentikasi</div>
        )}
      </div>

      {/* Steps indicator */}
      <div style={{ display: "flex", gap: 0, padding: "0 24px", background: "#15171C", borderBottom: "1px solid #2A2D35" }}>
        {[
          { key: "upload", label: "1. Upload", icon: "📁" },
          { key: "mapping", label: "2. Mapping", icon: "🔗" },
          { key: "rules", label: "3. Aturan Shift", icon: "⏰" },
          { key: "preview", label: "4. Preview", icon: "👁" },
          { key: "import", label: "5. Import", icon: "✅" },
        ].map((s, i) => (
          <div
            key={s.key}
            style={{
              padding: "12px 20px",
              fontSize: 13,
              fontWeight: step === s.key ? 600 : 400,
              color: step === s.key ? "#FF7A00" : "#666",
              borderBottom: step === s.key ? "2px solid #FF7A00" : "2px solid transparent",
              cursor: "pointer",
            }}
            onClick={() => {
              if (s.key === "upload") setStep("upload");
              if (s.key === "mapping" && importId) setStep("mapping");
              if (s.key === "rules" && importId) setStep("rules");
              if (s.key === "preview" && importId) setStep("preview");
              if (s.key === "import" && importId) setStep("import");
            }}
          >
            {s.icon} {s.label}
          </div>
        ))}
      </div>

      <div style={{ display: "flex", height: "calc(100vh - 120px)" }}>
        {/* Main content */}
        <div style={{ flex: 1, padding: 24, overflowY: "auto" }}>
          {/* ── UPLOAD STEP ── */}
          {step === "upload" && (
            <div style={{ maxWidth: 600, margin: "40px auto", textAlign: "center" }}>
              <div style={{ fontSize: 48, marginBottom: 16 }}>📊</div>
              <h2 style={{ fontSize: 24, fontWeight: 700, marginBottom: 8 }}>Upload Data Karyawan</h2>
              <p style={{ color: "#888", marginBottom: 32 }}>
                Upload file CSV atau Excel berisi data karyawan.<br />
                Format: Nama, Jabatan, No. HP, Shift, Lokasi, Koordinat
              </p>
              <div
                onClick={() => fileRef.current?.click()}
                style={{
                  border: "2px dashed #3A3D45",
                  borderRadius: 16,
                  padding: "48px 32px",
                  cursor: "pointer",
                  transition: "border-color 0.2s",
                  background: "#15171C",
                }}
                onMouseOver={(e) => (e.currentTarget.style.borderColor = "#FF7A00")}
                onMouseOut={(e) => (e.currentTarget.style.borderColor = "#3A3D45")}
              >
                {uploadProgress ? (
                  <div style={{ fontSize: 16, color: "#FF7A00" }}>⏳ Mengupload...</div>
                ) : (
                  <>
                    <div style={{ fontSize: 32, marginBottom: 12 }}>⬆️</div>
                    <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>Klik untuk upload</div>
                    <div style={{ fontSize: 13, color: "#666" }}>CSV, XLSX, XLS — maks 10MB</div>
                  </>
                )}
              </div>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,.xlsx,.xls"
                onChange={handleUpload}
                style={{ display: "none" }}
              />

              <div style={{ marginTop: 32, textAlign: "left", background: "#15171C", borderRadius: 12, padding: 20, border: "1px solid #2A2D35" }}>
                <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 14 }}>📋 Format CSV yang didukung:</div>
                <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid #2A2D35" }}>
                      <th style={{ padding: "6px 8px", textAlign: "left", color: "#888" }}>Kolom</th>
                      <th style={{ padding: "6px 8px", textAlign: "left", color: "#888" }}>Contoh</th>
                      <th style={{ padding: "6px 8px", textAlign: "left", color: "#888" }}>Wajib?</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ["Nama Karyawan", "Budi Santoso", "✓"],
                      ["Jabatan", "Operator Excavator", ""],
                      ["No. HP", "08123456789", "✓ salah satu"],
                      ["Kode/NIP", "EMP-001", "✓ salah satu"],
                      ["Shift", "Pagi / Malam", ""],
                      ["Lokasi", "Site A - Lavenue", ""],
                      ["Latitude", "-6.24832", ""],
                      ["Longitude", "106.84326", ""],
                    ].map(([col, ex, req], i) => (
                      <tr key={i} style={{ borderBottom: "1px solid #1A1D23" }}>
                        <td style={{ padding: "6px 8px" }}>{col}</td>
                        <td style={{ padding: "6px 8px", color: "#888" }}>{ex}</td>
                        <td style={{ padding: "6px 8px", color: req ? "#FF7A00" : "#555" }}>{req}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ── MAPPING STEP ── */}
          {step === "mapping" && (
            <div>
              <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>🔗 Mapping Kolom</h2>
              <p style={{ color: "#888", marginBottom: 20, fontSize: 13 }}>
                File: <strong>{filename}</strong> — {totalRows} baris ditemukan
              </p>

              <div style={{ background: "#15171C", borderRadius: 12, border: "1px solid #2A2D35", overflow: "hidden" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ background: "#1A1D23" }}>
                      <th style={{ padding: "10px 16px", textAlign: "left", fontSize: 13, fontWeight: 600 }}>Kolom CSV</th>
                      <th style={{ padding: "10px 16px", textAlign: "left", fontSize: 13, fontWeight: 600 }}>Mapping ke</th>
                      <th style={{ padding: "10px 16px", textAlign: "left", fontSize: 13, fontWeight: 600 }}>Preview</th>
                    </tr>
                  </thead>
                  <tbody>
                    {autoMapping.map((m, i) => (
                      <tr key={m.name} style={{ borderBottom: "1px solid #2A2D35" }}>
                        <td style={{ padding: "10px 16px", fontWeight: 500 }}>{m.name}</td>
                        <td style={{ padding: "10px 16px" }}>
                          <select
                            value={m.maps_to}
                            onChange={(e) => updateMapping(m.name, e.target.value)}
                            style={{
                              background: "#0B0B0B",
                              color: "#E0E0E0",
                              border: "1px solid #3A3D45",
                              borderRadius: 6,
                              padding: "6px 10px",
                              fontSize: 13,
                              width: "100%",
                            }}
                          >
                            {TARGET_FIELDS.map(f => (
                              <option key={f.value} value={f.value}>{f.label}</option>
                            ))}
                          </select>
                        </td>
                        <td style={{ padding: "10px 16px", fontSize: 12, color: "#666", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {preview[0]?.[m.name] || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Preview table */}
              <h3 style={{ fontSize: 16, fontWeight: 600, marginTop: 24, marginBottom: 12 }}>Preview Data (5 baris pertama)</h3>
              <div style={{ background: "#15171C", borderRadius: 12, border: "1px solid #2A2D35", overflow: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: "#1A1D23" }}>
                      {headers.map(h => (
                        <th key={h} style={{ padding: "8px 12px", textAlign: "left", whiteSpace: "nowrap" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.slice(0, 5).map((row, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid #2A2D35" }}>
                        {headers.map(h => (
                          <td key={h} style={{ padding: "8px 12px", whiteSpace: "nowrap", maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis" }}>
                            {row[h] || "—"}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={{ display: "flex", gap: 12, marginTop: 20 }}>
                <button onClick={() => setStep("upload")} style={{ background: "#2A2D35", color: "#E0E0E0", border: "none", borderRadius: 8, padding: "10px 20px", cursor: "pointer" }}>
                  ← Kembali
                </button>
                <button
                  onClick={handlePreview}
                  disabled={loading}
                  style={{ background: "#FF7A00", color: "#fff", border: "none", borderRadius: 8, padding: "10px 20px", cursor: "pointer", fontWeight: 600, flex: 1 }}
                >
                  {loading ? "⏳ Memvalidasi..." : "Validasi & Lanjut →"}
                </button>
              </div>
            </div>
          )}

          {/* ── RULES STEP ── */}
          {step === "rules" && (
            <div>
              <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>⏰ Aturan Shift Kerja</h2>
              <p style={{ color: "#888", marginBottom: 20, fontSize: 13 }}>
                {validRows} baris valid • {errorRows} error
              </p>

              {/* Existing rules */}
              {shiftRules.length > 0 && (
                <div style={{ marginBottom: 20 }}>
                  <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Aturan yang sudah ada:</div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {shiftRules.map(r => (
                      <button
                        key={r.id}
                        onClick={() => setSelectedRuleId(r.id)}
                        style={{
                          background: selectedRuleId === r.id ? "#FF7A00" : "#2A2D35",
                          color: selectedRuleId === r.id ? "#fff" : "#E0E0E0",
                          border: "none",
                          borderRadius: 8,
                          padding: "8px 16px",
                          cursor: "pointer",
                          fontSize: 13,
                        }}
                      >
                        {r.name} ({r.rules.shifts.map(s => s.name).join(", ")})
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* AI Chat */}
              <div style={{ background: "#15171C", borderRadius: 12, border: "1px solid #2A2D35", overflow: "hidden" }}>
                <div style={{ padding: "12px 16px", borderBottom: "1px solid #2A2D35", display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#4CAF50" }} />
                  <span style={{ fontWeight: 600, fontSize: 14 }}>🤖 AI Shift Assistant</span>
                  <span style={{ fontSize: 12, color: "#666", marginLeft: "auto" }}>Opsional — jelaskan aturan shift dalam bahasa natural</span>
                </div>

                {/* Chat messages */}
                <div style={{ height: 300, overflowY: "auto", padding: 16 }}>
                  {chatMessages.length === 0 && (
                    <div style={{ color: "#555", fontSize: 13, textAlign: "center", marginTop: 40 }}>
                      Jelaskan aturan shift kerja perusahaan Anda di sini.<br />
                      Contoh: "Karyawan 12 minggu di site, 2 minggu off. Shift pagi 07-19, malam 19-07. Maks 7 hari berturut-turut shift sama."
                    </div>
                  )}
                  {chatMessages.map((msg, i) => (
                    <div key={i} style={{ marginBottom: 12, display: "flex", justifyContent: msg.role === "user" ? "flex-end" : "flex-start" }}>
                      <div style={{
                        maxWidth: "80%",
                        padding: "10px 14px",
                        borderRadius: 12,
                        background: msg.role === "user" ? "#FF7A00" : "#2A2D35",
                        color: msg.role === "user" ? "#fff" : "#E0E0E0",
                        fontSize: 13,
                        whiteSpace: "pre-wrap",
                      }}>
                        {msg.content}
                      </div>
                    </div>
                  ))}
                  {chatLoading && (
                    <div style={{ color: "#888", fontSize: 13 }}>⏳ AI sedang memproses...</div>
                  )}
                  <div ref={chatEndRef} />
                </div>

                {/* Chat input */}
                <div style={{ padding: "12px 16px", borderTop: "1px solid #2A2D35", display: "flex", gap: 8 }}>
                  <input
                    value={chatInput}
                    onChange={(e) => setChatInput(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleChat()}
                    placeholder="Jelaskan aturan shift kerja..."
                    style={{
                      flex: 1,
                      background: "#0B0B0B",
                      color: "#E0E0E0",
                      border: "1px solid #3A3D45",
                      borderRadius: 8,
                      padding: "10px 14px",
                      fontSize: 13,
                      outline: "none",
                    }}
                  />
                  <button
                    onClick={handleChat}
                    disabled={chatLoading || !chatInput.trim()}
                    style={{
                      background: "#FF7A00",
                      color: "#fff",
                      border: "none",
                      borderRadius: 8,
                      padding: "10px 16px",
                      cursor: "pointer",
                      fontWeight: 600,
                    }}
                  >
                    Kirim
                  </button>
                </div>
              </div>

              {/* Parsed rules preview */}
              {parsedRuleset && (
                <div style={{ marginTop: 16, background: "#1A2D1A", borderRadius: 12, border: "1px solid #2D4A2D", padding: 16 }}>
                  <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8, color: "#4CAF50" }}>✅ Hasil Parsing AI:</div>
                  <div style={{ fontSize: 13 }}>
                    <div><strong>Shift:</strong> {parsedRuleset.shifts.map((s: any) => `${s.name} (${s.start_time}-${s.end_time})`).join(", ")}</div>
                    <div><strong>Rotasi:</strong> {parsedRuleset.rotation.map((r: any) => `${r.type}: ${JSON.stringify(r.params)}`).join(", ")}</div>
                    <div><strong>Batasan:</strong> {parsedRuleset.constraints.map((c: any) => `${c.type}: ${JSON.stringify(c.params)}`).join(", ")}</div>
                  </div>
                </div>
              )}

              {errors.length > 0 && (
                <div style={{ marginTop: 16, background: "#2D1A1A", borderRadius: 12, border: "1px solid #4A2D2D", padding: 16 }}>
                  <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8, color: "#F44336" }}>⚠️ Validation Errors:</div>
                  {errors.slice(0, 10).map((e, i) => (
                    <div key={i} style={{ fontSize: 12, color: "#E0E0E0", marginBottom: 4 }}>• {e}</div>
                  ))}
                  {errors.length > 10 && <div style={{ fontSize: 12, color: "#888" }}>... dan {errors.length - 10} error lainnya</div>}
                </div>
              )}

              <div style={{ display: "flex", gap: 12, marginTop: 20 }}>
                <button onClick={() => setStep("mapping")} style={{ background: "#2A2D35", color: "#E0E0E0", border: "none", borderRadius: 8, padding: "10px 20px", cursor: "pointer" }}>
                  ← Kembali
                </button>
                <button
                  onClick={() => setStep("preview")}
                  style={{ background: "#2A2D35", color: "#E0E0E0", border: "none", borderRadius: 8, padding: "10px 20px", cursor: "pointer" }}
                >
                  Skip (tanpa shift rule)
                </button>
                <button
                  onClick={() => { loadShiftRules(token); setStep("preview"); }}
                  disabled={!selectedRuleId && !parsedRuleset}
                  style={{ background: "#FF7A00", color: "#fff", border: "none", borderRadius: 8, padding: "10px 20px", cursor: "pointer", fontWeight: 600, flex: 1 }}
                >
                  Gunakan Aturan & Lanjut →
                </button>
              </div>
            </div>
          )}

          {/* ── PREVIEW STEP ── */}
          {step === "preview" && (
            <div>
              <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>👁 Preview & Konfigurasi Import</h2>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 20 }}>
                {/* Project selection */}
                <div style={{ background: "#15171C", borderRadius: 12, border: "1px solid #2A2D35", padding: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Project Tujuan</div>
                  <select
                    value={projectId}
                    onChange={(e) => setProjectId(e.target.value)}
                    style={{ width: "100%", background: "#0B0B0B", color: "#E0E0E0", border: "1px solid #3A3D45", borderRadius: 8, padding: "8px 12px", fontSize: 13 }}
                  >
                    <option value="">-- Pilih Project --</option>
                    {projects.map(p => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                </div>

                {/* Shift rule selection */}
                <div style={{ background: "#15171C", borderRadius: 12, border: "1px solid #2A2D35", padding: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Shift Rule</div>
                  <select
                    value={selectedRuleId}
                    onChange={(e) => setSelectedRuleId(e.target.value)}
                    style={{ width: "100%", background: "#0B0B0B", color: "#E0E0E0", border: "1px solid #3A3D45", borderRadius: 8, padding: "8px 12px", fontSize: 13 }}
                  >
                    <option value="">-- Tanpa Shift Rule --</option>
                    {shiftRules.map(r => (
                      <option key={r.id} value={r.id}>{r.name}</option>
                    ))}
                  </select>
                </div>
              </div>

              {selectedRuleId && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 20 }}>
                  <div style={{ background: "#15171C", borderRadius: 12, border: "1px solid #2A2D35", padding: 16 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Generate Jadwal Otomatis</div>
                    <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                      <input
                        type="checkbox"
                        checked={generateSchedule}
                        onChange={(e) => setGenerateSchedule(e.target.checked)}
                      />
                      <span style={{ fontSize: 13 }}>Ya, buat jadwal shift otomatis</span>
                    </label>
                  </div>
                  <div style={{ background: "#15171C", borderRadius: 12, border: "1px solid #2A2D35", padding: 16 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Durasi Jadwal (hari)</div>
                    <input
                      type="number"
                      value={scheduleDays}
                      onChange={(e) => setScheduleDays(parseInt(e.target.value) || 90)}
                      style={{ width: "100%", background: "#0B0B0B", color: "#E0E0E0", border: "1px solid #3A3D45", borderRadius: 8, padding: "8px 12px", fontSize: 13 }}
                    />
                  </div>
                </div>
              )}

              {/* Summary */}
              <div style={{ background: "#15171C", borderRadius: 12, border: "1px solid #2A2D35", padding: 16, marginBottom: 20 }}>
                <div style={{ display: "flex", gap: 24 }}>
                  <div><span style={{ color: "#888", fontSize: 12 }}>Total Baris</span><div style={{ fontSize: 24, fontWeight: 700 }}>{totalRows}</div></div>
                  <div><span style={{ color: "#888", fontSize: 12 }}>Valid</span><div style={{ fontSize: 24, fontWeight: 700, color: "#4CAF50" }}>{validRows}</div></div>
                  <div><span style={{ color: "#888", fontSize: 12 }}>Error</span><div style={{ fontSize: 24, fontWeight: 700, color: errorRows > 0 ? "#F44336" : "#888" }}>{errorRows}</div></div>
                </div>
              </div>

              {/* Preview table */}
              <div style={{ background: "#15171C", borderRadius: 12, border: "1px solid #2A2D35", overflow: "auto", marginBottom: 20 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: "#1A1D23" }}>
                      <th style={{ padding: "8px 12px", textAlign: "left" }}>#</th>
                      {preview[0] && Object.keys(preview[0]).map(k => (
                        <th key={k} style={{ padding: "8px 12px", textAlign: "left" }}>{k}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.slice(0, 20).map((row, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid #2A2D35" }}>
                        <td style={{ padding: "8px 12px", color: "#666" }}>{i + 1}</td>
                        {Object.values(row).map((v, j) => (
                          <td key={j} style={{ padding: "8px 12px" }}>{String(v || "—")}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={{ display: "flex", gap: 12 }}>
                <button onClick={() => setStep("rules")} style={{ background: "#2A2D35", color: "#E0E0E0", border: "none", borderRadius: 8, padding: "10px 20px", cursor: "pointer" }}>
                  ← Kembali
                </button>
                {selectedRuleId && generateSchedule && (
                  <button
                    onClick={handleGenerateSchedule}
                    disabled={loading || !projectId}
                    style={{ background: "#2196F3", color: "#fff", border: "none", borderRadius: 8, padding: "10px 20px", cursor: "pointer", fontWeight: 600 }}
                  >
                    {loading ? "⏳ Generate..." : "🔄 Generate Jadwal"}
                  </button>
                )}
                <button
                  onClick={handleConfirmImport}
                  disabled={loading || !projectId}
                  style={{ background: "#4CAF50", color: "#fff", border: "none", borderRadius: 8, padding: "10px 20px", cursor: "pointer", fontWeight: 600, flex: 1 }}
                >
                  {loading ? "⏳ Importing..." : "✅ Import Sekarang"}
                </button>
              </div>
            </div>
          )}

          {/* ── DONE STEP ── */}
          {step === "done" && importResult && (
            <div style={{ maxWidth: 500, margin: "60px auto", textAlign: "center" }}>
              <div style={{ fontSize: 64, marginBottom: 16 }}>🎉</div>
              <h2 style={{ fontSize: 24, fontWeight: 700, marginBottom: 8 }}>Import Berhasil!</h2>
              <div style={{ background: "#15171C", borderRadius: 12, border: "1px solid #2A2D35", padding: 24, marginTop: 24, textAlign: "left" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
                  <span style={{ color: "#888" }}>Karyawan terimport</span>
                  <span style={{ fontWeight: 700, fontSize: 18, color: "#4CAF50" }}>{importResult.imported}</span>
                </div>
                {importResult.errors?.length > 0 && (
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
                    <span style={{ color: "#888" }}>Error</span>
                    <span style={{ fontWeight: 700, color: "#F44336" }}>{importResult.errors.length}</span>
                  </div>
                )}
                {selectedRuleId && generateSchedule && (
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
                    <span style={{ color: "#888" }}>Jadwal digenerate</span>
                    <span style={{ fontWeight: 700, color: "#FF7A00" }}>{scheduleDays} hari</span>
                  </div>
                )}
              </div>
              <button
                onClick={() => { setStep("upload"); setImportId(""); setFilename(""); setPreview([]); setChatMessages([]); setParsedRuleset(null); setImportResult(null); }}
                style={{ marginTop: 24, background: "#FF7A00", color: "#fff", border: "none", borderRadius: 8, padding: "12px 24px", cursor: "pointer", fontWeight: 600, fontSize: 15 }}
              >
                Import Lagi
              </button>
            </div>
          )}
        </div>

        {/* Sidebar — AI Chat (always visible on rules step) */}
        {step === "rules" && (
          <div style={{ width: 400, borderLeft: "1px solid #2A2D35", background: "#0F1014", display: "none" }}>
            {/* Hidden on mobile, shown on desktop via CSS media query */}
          </div>
        )}
      </div>
    </div>
  );
}
