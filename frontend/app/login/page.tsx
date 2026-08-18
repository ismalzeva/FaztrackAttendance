"use client";

import {FormEvent, useEffect, useState} from "react";

type Membership = {tenant_id: string; tenant_name: string; role: string};
const api = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export default function Login() {
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [token, setToken] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (localStorage.getItem("faztrack_token") && localStorage.getItem("faztrack_tenant_id")) {
      setMessage("Sesi aktif ditemukan. Anda dapat langsung membuka workspace.");
    }
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage("");
    try {
      const response = await fetch(`${api}/auth/login`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({login_id: loginId, password}),
      });
      const body = await response.json();
      if (!response.ok) throw new Error("ID pengguna atau kata sandi tidak sesuai.");
      const accessToken = body.data.access_token;
      const me = await fetch(`${api}/me`, {headers: {Authorization: `Bearer ${accessToken}`}});
      const meBody = await me.json();
      if (!me.ok || !meBody.data.memberships.length) throw new Error("Pengguna belum memiliki workspace aktif.");
      setToken(accessToken); setMemberships(meBody.data.memberships);
      if (meBody.data.memberships.length === 1) choose(accessToken, meBody.data.memberships[0]);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Login gagal.");
    } finally { setBusy(false); }
  }

  function choose(accessToken: string, membership: Membership) {
    localStorage.setItem("faztrack_token", accessToken);
    localStorage.setItem("faztrack_tenant_id", membership.tenant_id);
    localStorage.setItem("faztrack_role", membership.role);
    window.location.href = membership.role === "ADMIN" || membership.role === "OWNER" ? "/timesheets" : "/review";
  }

  function openWorkspace() { window.location.href = "/timesheets"; }

  return <main className="workerPage"><section className="workerCard loginCard">
    <p className="eyebrow">Faztrack Attendance</p><h1>Masuk ke workspace</h1><p>Untuk admin dan supervisor Lumin Park Property.</p>
    <form onSubmit={submit}><label className="field"><span>ID pengguna</span><input autoComplete="username" value={loginId} onChange={event => setLoginId(event.target.value)}/></label><label className="field"><span>Kata sandi</span><input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)}/></label><button disabled={busy || !loginId || !password}>{busy ? "Memeriksa…" : "Masuk"}</button></form>
    {memberships.length > 1 && <div className="workspaceChoices"><b>Pilih perusahaan</b>{memberships.map(membership => <button className="secondary" key={`${membership.tenant_id}-${membership.role}`} onClick={() => choose(token, membership)}>{membership.tenant_name} · {membership.role}</button>)}</div>}
    {message && <div className="noticeBox">{message}{message.startsWith("Sesi aktif") && <button className="linkButton" onClick={openWorkspace}>Buka workspace</button>}</div>}
    <small>Pekerja melakukan absensi melalui halaman khusus dan tidak menggunakan login ini.</small>
  </section></main>;
}
