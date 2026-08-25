// Shared helpers for the Metro Mining demo frontend.
//
// API base URL resolution order:
//   1. window.__API_BASE__  (runtime injection, e.g. via <script>window.__API_BASE__=...</script>)
//   2. NEXT_PUBLIC_API_BASE_URL (build-time env var)
//   3. http://localhost:8084/api/v1 (Metro Mining backend default)
export function getApiBase(): string {
  if (typeof window !== "undefined" && (window as any).__API_BASE__) {
    return String((window as any).__API_BASE__);
  }
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8084/api/v1";
}

export const DEMO_DATE = "2026-09-01";
export const DEMO_ACCOUNTS = [
  {label: "Admin", login: "admin@metro-mining.id", password: "MetroDemo2026!"},
  {label: "Supervisor", login: "supervisor@metro-mining.id", password: "MetroDemo2026!"},
];

export type Membership = {tenant_id: string; tenant_name: string; role: string};

export function getToken(): string {
  return localStorage.getItem("faztrack_token") ?? "";
}
export function getTenantId(): string {
  return localStorage.getItem("faztrack_tenant_id") ?? "";
}

export async function apiFetch(path: string, init?: RequestInit): Promise<any> {
  const token = getToken();
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? {Authorization: `Bearer ${token}`} : {}),
      ...(getTenantId() ? {"X-Tenant-ID": getTenantId()} : {}),
      ...init?.headers,
    },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const msg =
      typeof detail === "string" ? detail :
      typeof detail?.message === "string" ? detail.message :
      response.status === 401 ? "Sesi berakhir. Silakan masuk kembali." :
      `Permintaan gagal (${response.status}).`;
    if (response.status === 401) {
      localStorage.removeItem("faztrack_token");
      localStorage.removeItem("faztrack_tenant_id");
      localStorage.removeItem("faztrack_role");
      window.location.href = "/login";
    }
    throw new Error(msg);
  }
  return body;
}

const STATE_LABEL: Record<string, string> = {
  SHIFT_COMPLETE: "Shift selesai",
  NOT_STARTED: "Belum absen",
  ABSENT: "Tidak hadir",
  REST: "Istirahat",
  OFFSITE: "Di luar site",
  PRESENT: "Hadir",
  OPEN: "Terbuka",
  ACKNOWLEDGED: "Ditanggapi",
  RESOLVED: "Selesai",
  WAIVED: "Dikecualikan",
  MATCH: "Cocok",
  MISMATCH: "Tidak cocok",
};
export function stateLabel(value?: string | null): string {
  return value ? (STATE_LABEL[value] ?? value) : "—";
}

export function stateClass(value?: string | null, kind: "op" | "exc" | "match" = "op"): string {
  const v = value ?? "";
  if (kind === "exc") return v === "OPEN" ? "state-absent" : v === "ACKNOWLEDGED" ? "state-pending" : "state-present";
  if (kind === "match") return /MATCH/.test(v) && !/MISMATCH|DISCREPANCY/.test(v) ? "state-present" : "state-absent";
  if (["SHIFT_COMPLETE", "PRESENT"].includes(v)) return "state-present";
  if (["ABSENT", "MISMATCH"].includes(v)) return "state-absent";
  if (["NOT_STARTED", "PENDING"].includes(v)) return "state-pending";
  return "state-pending";
}

/** WITA (Asia/Makassar, UTC+8) rendering of an ISO timestamp. */
export function witaTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString("id-ID", {timeZone: "Asia/Makassar", hour: "2-digit", minute: "2-digit"});
  } catch {
    return iso;
  }
}
export function witaDateTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("id-ID", {
      timeZone: "Asia/Makassar", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
