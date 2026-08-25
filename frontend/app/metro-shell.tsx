"use client";
import type {ReactNode} from "react";

const NAV = [
  {href: "/dashboard", label: "Dasbor"},
  {href: "/roster", label: "Roster Operasional"},
  {href: "/attendance", label: "Absensi & Checkpoint"},
  {href: "/exceptions", label: "Exception"},
];

/** Sidebar + workspace shell for all Metro demo pages. */
export default function Shell({active, title, eyebrow, subtitle, badge, children}: {
  active: string;
  title: string;
  eyebrow: string;
  subtitle: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  function logout() {
    ["faztrack_token", "faztrack_tenant_id", "faztrack_role"].forEach(k => localStorage.removeItem(k));
    window.location.href = "/login";
  }
  return <main className="shell">
    <aside>
      <div className="brand">Faztrack <span>Attendance</span></div>
      <p className="tenantTag">Metro Mining · Padang Mine</p>
      <nav>
        {NAV.map(item => <a key={item.href} href={item.href} className={active === item.href ? "active" : ""}>{item.label}</a>)}
      </nav>
      <button className="logoutBtn" onClick={logout}>Keluar</button>
      <small>Demo operasional · 01 Sep 2026</small>
    </aside>
    <section className="workspace">
      <header>
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </div>
        {badge}
      </header>
      {children}
    </section>
  </main>;
}

export function ErrorBox({message}: {message: string}) {
  return <p className="error">{message}</p>;
}
