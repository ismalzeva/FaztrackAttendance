#!/usr/bin/env python3
"""
fzctl — Faztrack Attendance client/tenant lifecycle CLI.

Single operator tool for provisioning, backing up, restoring, and listing
Faztrack Attendance client instances. Implements the multi-company
architecture decision (docs/FAZTRACK_MULTI_COMPANY_ARCHITECTURE.md):
one shared core, strict tenant isolation.

Subcommands:
    fzctl list                              list registered tenants
    fzctl info --slug <slug>                show one tenant
    fzctl onboard --slug <slug> ...         provision a new client instance
    fzctl backup --slug <slug>              dump DB + env to backups/<slug>/
    fzctl restore --slug <slug> --file <p>  restore a DB dump

Tier isolation:
    enterprise   dedicated postgres container per client (DB-level isolation)
    smb          shared postgres container, one DATABASE per client

Secrets are generated here, written to backend/.env.<slug> (mode 0600), and are
NEVER printed to stdout or the terminal log. Backups/ and .env.* are gitignored.

Usage (dry-run first, always):
    python3 scripts/fzctl.py onboard --slug acme --name "Acme Corp" --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import NoReturn

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO, "scripts")
BACKEND_DIR = os.path.join(REPO, "backend")
REGISTRY_PATH = os.path.join(SCRIPTS_DIR, "tenants.json")
TEMPLATES_DIR = os.path.join(SCRIPTS_DIR, "templates")
BACKUPS_DIR = os.path.join(REPO, "backups")

PG_IMAGE = "postgres:16-alpine"
SMTP_CONTAINER = "faztrack-smb-postgres"  # shared postgres for smb tier

DEFAULT_FLOOR = {"db": 5438, "backend": 8085, "frontend": 3012}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _err(msg: str) -> "NoReturn":
    print(f"[fzctl] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _log(msg: str):
    print(f"[fzctl] {msg}")


def load_registry(path: str = REGISTRY_PATH) -> dict:
    with open(path) as fh:
        return json.load(fh)


def save_registry(data: dict, path: str = REGISTRY_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def find_tenant(registry: dict, slug: str) -> dict | None:
    for t in registry.get("tenants", []):
        if t["slug"] == slug:
            return t
    return None


def render(template: str, ctx: dict) -> str:
    out = template
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v))
    leftover = [w for w in ("{{", "}}") if w in out]
    if "{{" in out and "}}" in out:
        import re

        missing = sorted(set(re.findall(r"\{\{(\w+)\}\}", out)))
        _err(f"template masih punya placeholder tak terisi: {missing}")
    return out


def _secret(nbytes: int = 32) -> str:
    return secrets.token_hex(nbytes)


def _pass(nbytes: int = 12) -> str:
    return secrets.token_urlsafe(nbytes)


def _pin() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def run(cmd: list[str], dry: bool = False, mask: list[str] | None = None,
        check: bool = True, capture: bool = False, env: dict | None = None,
        cwd: str | None = None):
    """Run a command. In dry-run, just echo it."""
    mask = mask or []
    shown = []
    for c in cmd:
        if any(m and m in c for m in mask):
            shown.append("<REDACTED>")
        else:
            shown.append(c)
    if dry:
        print("[dry-run] $ " + " ".join(shown))
        return None
    joined_only_for_log = " ".join(shown)
    _log("$ " + joined_only_for_log)
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True, env=env,
                              check=check, cwd=cwd)
    return subprocess.run(cmd, env=env, check=check, cwd=cwd)


# ---------------------------------------------------------------------------
# port allocation
# ---------------------------------------------------------------------------
def _used(registry: dict, key: str) -> set[int]:
    out = set()
    for t in registry.get("tenants", []):
        if key == "db":
            out.add(t["db"]["port"])
        else:
            out.add(t[key]["port"])
    # Also include smb shared container port (if any) for db key
    if key == "db":
        smb = registry.get("smb_shared", {})
        if smb.get("host_port"):
            out.add(smb["host_port"])
    return out


def next_port(registry: dict, key: str) -> int:
    floor = DEFAULT_FLOOR[key]
    used = _used(registry, key)
    p = floor
    while p in used:
        p += 1
    return p


# ---------------------------------------------------------------------------
# ctx builders
# ---------------------------------------------------------------------------
def build_ctx(registry: dict, args) -> dict:
    slug = args.slug
    name = args.name
    code = args.code or slug.replace("-", "_").upper()
    timezone_ = args.timezone or "Asia/Jakarta"
    tier = args.tier
    admin_login = args.admin_login or f"admin.{slug}"
    admin_name = args.admin_name or f"Admin {name}"

    # DB
    if tier == "enterprise":
        db_container = f"{slug}-postgres"
        db_name = f"faztrack_attendance_{slug}"
        db_user = f"faztrack_{slug}"
        db_port = args.db_port or next_port(registry, "db")
    else:  # smb
        smb = registry["smb_shared"]
        db_container = smb["container"]
        db_name = f"faztrack_attendance_{slug}"
        db_user = f"faztrack_{slug}"
        db_port = smb["host_port"]

    backend_port = args.backend_port or next_port(registry, "backend")
    frontend_port = args.frontend_port or next_port(registry, "frontend")
    frontend_dir = args.frontend_dir or os.path.expanduser(f"~/{slug}-frontend")

    env_file = os.path.join(BACKEND_DIR, f".env.{slug}")
    venv_dir = args.venv_dir or os.path.join(BACKEND_DIR, ".venv")
    backend_service = f"faztrack-attendance-{slug}.service"
    frontend_service = f"faztrack-attendance-{slug}-web.service"
    tenant_id = f"{slug}-001"

    cors_origins = args.cors or "http://localhost:" + str(frontend_port)
    api_url = args.api_url or f"http://localhost:{backend_port}"

    ctx = {
        "slug": slug,
        "name": name,
        "code": code,
        "tier": tier,
        "vertical": args.vertical or "",
        "timezone": timezone_,
        "admin_login": admin_login,
        "admin_name": admin_name,
        "tenant_id": tenant_id,
        "db_container": db_container,
        "db_name": db_name,
        "db_user": db_user,
        "db_port": db_port,
        "db_password": _pass(),
        "backend_port": backend_port,
        "frontend_port": frontend_port,
        "backend_service": backend_service,
        "frontend_service": frontend_service,
        "backend_dir": BACKEND_DIR,
        "frontend_dir": frontend_dir,
        "env_file": env_file,
        "venv_dir": venv_dir,
        "cors_origins": cors_origins,
        "api_url": api_url,
        "domain": args.domain or "",
        "api_domain": args.api_domain or "",
        # secrets
        "jwt_secret": _secret(32),
        "seed_password": _pass(),
        "worker_pin": _pin(),
    }
    # template placeholders use UPPERCASE; seed stub also wants TENANT_CODE.
    ctx["tenant_code"] = ctx["code"]
    for _k in list(ctx.keys()):
        ctx.setdefault(_k.upper(), ctx[_k])
    return ctx


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_list(args):
    registry = load_registry()
    print(f"{'SLUG':<9} {'NAME':<24} {'TIER':<11} {'TZ':<15} "
          f"{'DB':<20} {'BE':<6} {'FE':<6} {'STATUS'}")
    for t in registry["tenants"]:
        db = f"{t['db']['name']}@{t['db']['port']}"
        print(f"{t['slug']:<9} {t['name']:<24} {t['tier']:<11} "
              f"{t['timezone']:<15} {db:<20} "
              f"{t['backend']['port']:<6} {t['frontend']['port']:<6} {t['status']}")


def cmd_info(args):
    registry = load_registry()
    t = find_tenant(registry, args.slug)
    if not t:
        _err(f"tenant '{args.slug}' tidak terdaftar")
    print(json.dumps(t, indent=2, ensure_ascii=False))


def _render_env(ctx: dict) -> str:
    """Render backend/.env.<slug> (mode 0600). Returns its path."""
    src = os.path.join(TEMPLATES_DIR, "env.template")
    with open(src) as fh:
        content = render(fh.read(), ctx)
    dest = os.path.join(BACKEND_DIR, f".env.{ctx['slug']}")
    with open(dest, "w") as fh:
        fh.write(content)
    os.chmod(dest, 0o600)
    print(f"[render] env.template -> {dest} (0600)")
    return dest


def _render_units(ctx: dict, out_dir: str) -> dict:
    """Render the two systemd unit files into out_dir (temp). Returns {service: path}."""
    os.makedirs(out_dir, exist_ok=True)
    mapping = {
        "systemd-backend.service.template": ctx["backend_service"],
        "systemd-frontend.service.template": ctx["frontend_service"],
    }
    result = {}
    for tmpl, svc in mapping.items():
        src = os.path.join(TEMPLATES_DIR, tmpl)
        with open(src) as fh:
            content = render(fh.read(), ctx)
        dest = os.path.join(out_dir, svc)
        with open(dest, "w") as fh:
            fh.write(content)
        print(f"[render] {tmpl} -> {dest}")
        result[svc] = dest
    return result


def cmd_onboard(args):
    registry = load_registry()
    if find_tenant(registry, args.slug):
        _err(f"tenant '{args.slug}' sudah terdaftar — gunakan 'list'/'info'")

    ctx = build_ctx(registry, args)
    tier = ctx["tier"]

    print("\n=== Faztrack Attendance — onboarding plan ===")
    print(f"slug         : {ctx['slug']}")
    print(f"name         : {ctx['name']}")
    print(f"code         : {ctx['code']}")
    print(f"tier         : {tier}")
    print(f"timezone     : {ctx['timezone']}")
    print(f"db           : {ctx['db_name']} (user {ctx['db_user']}) "
          f"via container {ctx['db_container']}:{ctx['db_port']}")
    print(f"backend      : port {ctx['backend_port']}")
    print(f"frontend     : port {ctx['frontend_port']} @ {ctx['frontend_dir']}")
    print(f"admin login  : {ctx['admin_login']}")
    print(f"env file     : {ctx['env_file']}")
    print("secrets      : generated (jwt/db/admin password/worker PIN) — REDACTED")
    print("=" * 52)

    if args.dry_run:
        print("\n[dry-run] tidak ada aksi nyata. Jalankan tanpa --dry-run untuk eksekusi.")
        return

    # 1. DB provisioning
    if tier == "enterprise":
        run(["docker", "run", "-d", "--name", ctx["db_container"],
             "--restart", "unless-stopped",
             "-e", f"POSTGRES_USER={ctx['db_user']}",
             "-e", f"POSTGRES_DB={ctx['db_name']}",
             "-e", f"POSTGRES_PASSWORD={ctx['db_password']}",
             "-v", f"{ctx['slug']}_pg:/var/lib/postgresql/data",
             "-p", f"127.0.0.1:{ctx['db_port']}:5432",
             PG_IMAGE],
            mask=[ctx["db_password"]], dry=False)
    else:
        _ensure_smb_container(registry, ctx)

    # 2. write .env
    _render_env(ctx)

    # 3. migrate schema (alembic reads FAZTRACK_DATABASE_URL from process env)
    env = os.environ.copy()
    env["FAZTRACK_DATABASE_URL"] = _db_url(ctx)
    run([os.path.join(ctx["venv_dir"], "bin", "alembic"), "upgrade", "head"],
        env=env, cwd=BACKEND_DIR, dry=False)

    # 4. systemd units (render to temp, install to /etc/systemd/system)
    tmpdir = tempfile.mkdtemp(prefix=f"fzctl-{ctx['slug']}-")
    try:
        units = _render_units(ctx, tmpdir)
        for svc, path in units.items():
            run(["sudo", "cp", path, f"/etc/systemd/system/{svc}"], dry=False)
        run(["sudo", "systemctl", "daemon-reload"], dry=False)
        for svc in units:
            run(["sudo", "systemctl", "enable", svc], dry=False)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # 5. minimal seed (tenant + admin) + start backend
    if not args.no_seed:
        seed_path = _write_seed_stub(ctx)
        run([os.path.join(ctx["venv_dir"], "bin", "python"), seed_path],
            env=env, cwd=BACKEND_DIR, dry=False)
    run(["sudo", "systemctl", "start", ctx["backend_service"]], dry=False)

    # 6. frontend build (heavy). Skip with --skip-frontend.
    if not args.skip_frontend:
        _build_frontend(ctx)

    # 7. register in registry
    registry["tenants"].append(_ctx_to_record(ctx))
    save_registry(registry)

    print("\n=== onboarding SELESAI ===")
    print(f"admin login : {ctx['admin_login']}")
    print(f"admin pass  : lihat {ctx['env_file']} -> FAZTRACK_DEMO_SEED_PASSWORD")
    print(f"worker PIN  : lihat {ctx['env_file']} -> FAZTRACK_DEMO_WORKER_PIN")
    print(f"backend     : http://localhost:{ctx['backend_port']}")
    print(f"frontend    : http://localhost:{ctx['frontend_port']}")
    if ctx.get("domain"):
        print(f"domain      : {ctx['domain']} (DNS/Caddy perlu diatur manual)")


def _db_url(ctx) -> str:
    scheme = "postgresql+psycopg"
    return (f"{scheme}://{ctx['db_user']}:{ctx['db_password']}"
            f"@localhost:{ctx['db_port']}/{ctx['db_name']}")


def _ensure_smb_container(registry: dict, ctx: dict):
    smb = registry["smb_shared"]
    name = smb["container"]
    # is container running?
    r = subprocess.run(["docker", "ps", "-q", "--filter", f"name={name}"],
                       capture_output=True, text=True)
    if r.stdout.strip():
        _log(f"shared smb container '{name}' sudah jalan")
    else:
        # container not running — create it (superuser password stored in env file)
        sup_pass = ctx.get("smb_superuser_password") or _pass()
        env_file = os.path.join(BACKEND_DIR, ".env.smb-postgres")
        run(["docker", "run", "-d", "--name", name, "--restart", "unless-stopped",
             "-e", "POSTGRES_USER=" + smb["superuser"],
             "-e", f"POSTGRES_PASSWORD={sup_pass}",
             "-e", "POSTGRES_DB=" + smb["superuser"],
             "-v", "smb_pg:/var/lib/postgresql/data",
             "-p", f"127.0.0.1:{smb['host_port']}:5432",
             PG_IMAGE], mask=[sup_pass], dry=False)
        with open(env_file, "w") as fh:
            fh.write(f"SMB_SUPERUSER={smb['superuser']}\n")
            fh.write(f"SMB_SUPERUSER_PASSWORD={sup_pass}\n")
        os.chmod(env_file, 0o600)
        _log(f"shared smb container dibuat; superuser creds -> {env_file}")

    # create role + database inside shared container
    db_name = ctx["db_name"]
    db_user = ctx["db_user"]
    db_pass = ctx["db_password"]
    run(["docker", "exec", name, "psql", "-U", smb["superuser"], "-c",
         f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{db_user}') "
         f"THEN CREATE ROLE {db_user} LOGIN PASSWORD '{db_pass}'; END IF; END $$;"],
        mask=[db_pass], dry=False)
    exists = subprocess.run(
        ["docker", "exec", name, "psql", "-U", smb["superuser"], "-tAc",
         f"SELECT 1 FROM pg_database WHERE datname='{db_name}'"],
        capture_output=True, text=True).stdout.strip()
    if not exists:
        run(["docker", "exec", name, "psql", "-U", smb["superuser"], "-c",
             f"CREATE DATABASE {db_name} OWNER {db_user}"], dry=False)
    _log(f"smb database '{db_name}' role '{db_user}' siap")


def _write_seed_stub(ctx: dict) -> str:
    src = os.path.join(TEMPLATES_DIR, "seed_stub.py.template")
    with open(src) as fh:
        content = render(fh.read(), ctx)
    dest = os.path.join(BACKEND_DIR, "scripts", f"seed_{ctx['slug']}_standalone.py")
    with open(dest, "w") as fh:
        fh.write(content)
    os.chmod(dest, 0o755)
    _log(f"seed stub -> {dest}")
    return dest


def _build_frontend(ctx: dict, start: bool = True):
    """Provision a SEPARATE frontend tree per tenant and build it.

    PITFALL (documented): building in `frontend/` would overwrite the shared
    `.next/standalone` used by another instance (NEXT_PUBLIC_API_BASE_URL is
    baked at build time). So each tenant gets its own tree at `~/.<slug>-frontend`.
    """
    src_frontend = os.path.join(REPO, "frontend")
    dest = ctx["frontend_dir"]

    _log(f"frontend: {src_frontend} -> {dest}")

    if not os.path.exists(dest):
        run(["rsync", "-a", "--exclude", "node_modules", "--exclude", ".next",
             "--exclude", ".git", src_frontend.rstrip("/") + "/", dest + "/"],
            dry=False)

    api_url = ctx.get("api_url") or ""
    if args_domain := ctx.get("api_domain"):
        api_url = f"https://{args_domain}"

    env = os.environ.copy()
    env["NEXT_PUBLIC_API_BASE_URL"] = api_url

    run(["npm", "ci"], cwd=dest, env=env, dry=False)
    run(["npm", "run", "build"], cwd=dest, env=env, dry=False)

    # PITFALL: standalone build omits .next/static and public/ -> CSS/asset 404.
    run(["bash", "-c",
         f"cp -r .next/static .next/standalone/.next/ 2>/dev/null || true; "
         f"cp -r public .next/standalone/ 2>/dev/null || true"],
        cwd=dest, dry=False)

    _log(f"frontend build selesai: {dest} (port {ctx['frontend_port']})")
    if start:
        run(["sudo", "systemctl", "start", ctx["frontend_service"]], dry=False)


def cmd_build_frontend(args):
    registry = load_registry()
    t = find_tenant(registry, args.slug)
    if not t:
        # allow ad-hoc build from registry-shaped tenant dict? require registered.
        _err(f"tenant '{args.slug}' tidak terdaftar — jalankan 'onboard' dulu")
    ctx = {
        "slug": t["slug"], "name": t["name"], "tier": t["tier"],
        "frontend_dir": t["frontend"]["dir"],
        "frontend_port": t["frontend"]["port"],
        "frontend_service": t["frontend"]["service"],
        "api_url": args.api_url or "",
        "api_domain": args.api_domain or "",
    }
    _build_frontend(ctx, start=not args.no_start)


def _ctx_to_record(ctx: dict) -> dict:
    return {
        "slug": ctx["slug"],
        "name": ctx["name"],
        "code": ctx["code"],
        "tier": ctx["tier"],
        "vertical": ctx.get("vertical", ""),
        "timezone": ctx["timezone"],
        "db": {
            "container": ctx["db_container"],
            "name": ctx["db_name"],
            "user": ctx["db_user"],
            "port": ctx["db_port"],
        },
        "backend": {"port": ctx["backend_port"], "service": ctx["backend_service"]},
        "frontend": {
            "port": ctx["frontend_port"],
            "service": ctx["frontend_service"],
            "dir": ctx["frontend_dir"],
        },
        "status": "active",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def cmd_backup(args):
    registry = load_registry()
    t = find_tenant(registry, args.slug)
    if not t:
        _err(f"tenant '{args.slug}' tidak terdaftar")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUPS_DIR, args.slug)
    os.makedirs(dest, exist_ok=True)

    container = t["db"]["container"]
    db_name = t["db"]["name"]
    db_user = t["db"]["user"]
    dump = os.path.join(dest, f"{db_name}-{ts}.sql.gz")

    _log(f"dump {db_name} dari container {container} ...")
    with open(dump, "wb") as fh:
        p1 = subprocess.Popen(["docker", "exec", container, "pg_dump",
                               "-U", db_user, "-d", db_name],
                              stdout=subprocess.PIPE)
        assert p1.stdout is not None
        p2 = subprocess.Popen(["gzip", "-9"], stdin=p1.stdout, stdout=fh)
        p1.stdout.close()
        p2.communicate()

    # backup env file too (secrets need restoring alongside DB)
    env_src = os.path.join(BACKEND_DIR, f".env.{args.slug}")
    if os.path.exists(env_src):
        env_dst = os.path.join(dest, f".env.{args.slug}.{ts}")
        shutil.copy2(env_src, env_dst)
        os.chmod(env_dst, 0o600)
        _log(f"env backup -> {env_dst}")

    _log(f"backup selesai: {dump}")
    print(f"LISTING {dest}:")
    for f in sorted(os.listdir(dest)):
        print("  " + f)


def cmd_restore(args):
    registry = load_registry()
    t = find_tenant(registry, args.slug)
    if not t:
        _err(f"tenant '{args.slug}' tidak terdaftar")
    if not os.path.exists(args.file):
        _err(f"file dump tidak ada: {args.file}")

    container = t["db"]["container"]
    db_name = t["db"]["name"]
    db_user = t["db"]["user"]

    confirm = input(
        f"RESTORE {db_name} (container {container}) dari {args.file}? "
        f"Data existing akan DITIMPA. Ketik {args.slug.upper()} untuk lanjut: "
    )
    if confirm.strip().upper() != args.slug.upper():
        _err("dibatalkan")

    _log("menghapus schema existing ...")
    run(["docker", "exec", container, "psql", "-U", db_user, "-d", db_name,
         "-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"], check=True)

    _log("restore dump ...")
    with open(args.file, "rb") as fh:
        p1 = subprocess.Popen(["gzip", "-dc"], stdin=fh, stdout=subprocess.PIPE)
        assert p1.stdout is not None
        p2 = subprocess.Popen(["docker", "exec", "-i", container, "psql",
                               "-U", db_user, "-d", db_name],
                              stdin=p1.stdout)
        p1.stdout.close()
        p2.communicate()
    _log("restore selesai")


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(prog="fzctl", description="Faztrack Attendance tenant lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list tenants")
    p_list.set_defaults(func=cmd_list)

    p_info = sub.add_parser("info", help="show one tenant")
    p_info.add_argument("--slug", required=True)
    p_info.set_defaults(func=cmd_info)

    p_on = sub.add_parser("onboard", help="provision a new client instance")
    p_on.add_argument("--slug", required=True, help="short url-safe id, e.g. acme")
    p_on.add_argument("--name", required=True, help="display name, e.g. 'Acme Corp'")
    p_on.add_argument("--code", help="tenant code (default: SLUG upper)")
    p_on.add_argument("--tier", choices=["enterprise", "smb"], default="enterprise",
                      help="enterprise=dedicated DB container (default); smb=shared DB container")
    p_on.add_argument("--timezone", help="IANA tz, e.g. Asia/Jakarta")
    p_on.add_argument("--vertical", help="mining / property / retail / ...")
    p_on.add_argument("--admin-login", help="admin login id (default admin.<slug>)")
    p_on.add_argument("--admin-name", help="admin display name")
    p_on.add_argument("--db-port", type=int, help="override DB host port")
    p_on.add_argument("--backend-port", type=int, help="override backend port")
    p_on.add_argument("--frontend-port", type=int, help="override frontend port")
    p_on.add_argument("--frontend-dir", help="override frontend build dir")
    p_on.add_argument("--venv-dir", help="override venv dir")
    p_on.add_argument("--cors", help="override CORS origins")
    p_on.add_argument("--api-url", help="override frontend API base url")
    p_on.add_argument("--domain", help="app domain (for Caddy/DNS note)")
    p_on.add_argument("--api-domain", help="API domain (for Caddy/DNS note)")
    p_on.add_argument("--no-seed", action="store_true", help="skip minimal admin seed")
    p_on.add_argument("--skip-frontend", action="store_true", help="skip frontend build")
    p_on.add_argument("--dry-run", action="store_true", help="print plan, no side effects")
    p_on.set_defaults(func=cmd_onboard)

    p_bf = sub.add_parser("build-frontend", help="build frontend for a tenant")
    p_bf.add_argument("--slug", required=True)
    p_bf.add_argument("--api-url", help="override NEXT_PUBLIC_API_BASE_URL")
    p_bf.add_argument("--api-domain", help="set API_BASE_URL from domain (https://<domain>)")
    p_bf.add_argument("--no-start", action="store_true", help="do not start frontend service")
    p_bf.set_defaults(func=cmd_build_frontend)

    p_bk = sub.add_parser("backup", help="dump DB + env")
    p_bk.add_argument("--slug", required=True)
    p_bk.set_defaults(func=cmd_backup)

    p_rs = sub.add_parser("restore", help="restore a DB dump")
    p_rs.add_argument("--slug", required=True)
    p_rs.add_argument("--file", required=True)
    p_rs.set_defaults(func=cmd_restore)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
