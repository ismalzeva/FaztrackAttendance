#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-v4.2-mutation-tests.sh
# REAL execute-mode mutation/failure/recovery assertions.
# Fake systemctl/curl + throwaway fixtures. Never touches production.
# ─────────────────────────────────────────────────────────
set -uo pipefail
SCRIPTS="/home/ubuntu/FaztrackAttendance/backend/scripts"
BASE="/tmp/lumin-mut-v42"
PASS=0; FAIL=0

t() {
  local name="$1" expect="$2" actual="$3"
  if [ "$expect" = "$actual" ]; then echo "PASS $name"; PASS=$((PASS+1))
  else echo "FAIL $name (expected=$expect got=$actual)"; FAIL=$((FAIL+1)); fi
}

build() {
  local root="$1"
  rm -rf "$root" 2>/dev/null
  mkdir -p "$root/bin" "$root/app/backend/app" "$root/app/backend/.venv/bin" \
           "$root/app/frontend/.next/standalone" \
           "$root/app/frontend/.next/static/chunks/app/admin" \
           "$root/app/frontend/.next/static/chunks/app/dashboard" \
           "$root/app/frontend/.next/static/chunks/app/absen" \
           "$root/app/releases" "$root/backup/exact-id" "$root/artifacts" \
           "$root/app/backend/uploads"

  # ---- OLD release (live) ----
  echo "OLD_BACKEND_MARKER" > "$root/app/backend/app/main.py"
  printf '#!/bin/sh\nexit 0\n' > "$root/app/backend/.venv/bin/uvicorn"
  chmod +x "$root/app/backend/.venv/bin/uvicorn"
  echo "OLDENV" > "$root/app/backend/.env.lumin"
  echo "OLDBUILD" > "$root/app/frontend/.next/BUILD_ID"
  echo "OLDSRV" > "$root/app/frontend/.next/standalone/server.js"
  echo "OLDADMIN" > "$root/app/frontend/.next/static/chunks/app/admin/page-oldadmin.js"
  echo "OLDDASH"  > "$root/app/frontend/.next/static/chunks/app/dashboard/page-olddash.js"
  echo "OLDABSEN" > "$root/app/frontend/.next/static/chunks/app/absen/page-oldabsen.js"
  echo "OLDENVLOCAL" > "$root/app/frontend/.env.local"
  echo "oldpersist" > "$root/app/backend/uploads/f.txt"

  # ---- ARTIFACT payloads ----
  mkdir -p "$root/payload-be/backend/app"
  echo "NEW_BACKEND_MARKER" > "$root/payload-be/backend/app/main.py"

  mkdir -p "$root/payload-fe/.next/standalone" \
           "$root/payload-fe/.next/static/chunks/app/admin" \
           "$root/payload-fe/.next/static/chunks/app/dashboard" \
           "$root/payload-fe/.next/static/chunks/app/absen"
  echo "S0kC8_NAlhQyCLKMFHHdQ" > "$root/payload-fe/.next/BUILD_ID"
  echo "NEWSRV" > "$root/payload-fe/.next/standalone/server.js"
  echo "NEWADMIN" > "$root/payload-fe/.next/static/chunks/app/admin/page-7ef835f4a59d5f3e.js"
  echo "NEWDASH"  > "$root/payload-fe/.next/static/chunks/app/dashboard/page-18c48464202db5cb.js"
  echo "NEWABSEN" > "$root/payload-fe/.next/static/chunks/app/absen/page-b864c4195106e108.js"
  echo "NEWENVLOCAL" > "$root/payload-fe/.env.local"

  ( cd "$root/payload-be" && tar czf "$root/artifacts/lumin-backend-413720b1.tar.gz" backend )
  ( cd "$root/payload-fe" && tar czf "$root/artifacts/lumin-frontend-6e3a3e20.tar.gz" .next )

  # ---- fake systemctl ----
  cat > "$root/bin/systemctl" << 'EOF'
#!/bin/sh
act="$1"; svc="$2"
[ -n "${FAKE_FAIL_START_BACKEND:-}" ] && [ "$act" = "start" ] && case "$svc" in *lumin.service) exit 1;; esac
[ -n "${FAKE_FAIL_START_FRONTEND:-}" ] && [ "$act" = "start" ] && case "$svc" in *lumin-web.service) exit 1;; esac
# Simulate post-switch persistent corruption when starting backend
if [ -n "${FAKE_CORRUPT_PERSIST:-}" ] && [ "$act" = "start" ]; then
  case "$svc" in *lumin.service)
    if [ -n "${FAKE_PERSIST_FILE:-}" ] && [ -f "$FAKE_PERSIST_FILE" ]; then
      echo "CORRUPTED" >> "$FAKE_PERSIST_FILE"
    fi
  ;; esac
fi
[ "$act" = "is-active" ] && { echo "active"; exit 0; }
exit 0
EOF
  chmod +x "$root/bin/systemctl"

  printf '#!/bin/sh\nexec "$@"\n' > "$root/bin/sudo"
  chmod +x "$root/bin/sudo"

  cat > "$root/bin/curl" << 'EOF'
#!/bin/sh
url=""
for a in "$@"; do case "$a" in http*) url="$a";; esac; done
[ -n "${FAKE_FAIL_PUBLIC:-}" ] && case "$url" in *gofaztrack.com*) exit 22;; esac
[ -n "${FAKE_FAIL_BACKEND_HEALTH:-}" ] && case "$url" in *:8011*) exit 22;; esac
echo "200"; exit 0
EOF
  chmod +x "$root/bin/curl"

  printf '#!/bin/sh\nexit 0\n' > "$root/bin/docker"
  chmod +x "$root/bin/docker"

  # ---- backup fixture (must satisfy V4.2 mandatory gates) ----
  local bk="$root/backup/exact-id"
  cat > "$bk/BACKUP-MANIFEST.md" << EOF
# BACKUP MANIFEST
Hostname: $(hostname)
## Validation Status: PASS
EOF
  echo "dump" > "$bk/db-dump-x.dump"
  cat > "$bk/VERIFICATION-RESULT.txt" << EOF
VERIFICATION-RESULT: PASS
verified_at: fixture
EOF
  ( cd "$bk" && find . -type f -not -name "checksums-sha256.txt" -printf "%P\n" | sort | xargs sha256sum > checksums-sha256.txt )
}

deploy() {  # $1=root, rest=env assignments
  local root="$1"; shift
  ( cd "$root/artifacts" && env PATH="$root/bin:$PATH" LUMIN_FIXTURE_ROOT="$root" "$@" \
      bash "$SCRIPTS/lumin-prod-deploy.sh" --execute --backup-dir "$root/backup/exact-id" )
}

pair_ok() { # both components present AND non-empty
  local r="$1"
  [ -s "$r/app/backend/app/main.py" ] && [ -s "$r/app/frontend/.next/BUILD_ID" ] && echo yes || echo no
}
release_dirs() { find "$1/app/releases" -maxdepth 1 -type d -name 'release-pair-*' 2>/dev/null | wc -l | tr -d ' '; }
failed_dirs()  { find "$1/app/releases" -maxdepth 1 -type d -name 'failed-*'       2>/dev/null | wc -l | tr -d ' '; }

echo "=== LUMIN V4.2 MUTATION TESTS ==="
echo ""

# ══════════════════════════════════════════════════════════
# M1 — backend start fails -> paired rollback, old pair restored
# ══════════════════════════════════════════════════════════
R="$BASE/m1"; build "$R"
FAKE_FAIL_START_BACKEND=1 deploy "$R" >/dev/null 2>&1; rc=$?
t "M1 exit non-zero"                       "1" "$rc"
t "M1 pair restored (both present)"        "yes" "$(pair_ok "$R")"
t "M1 old backend content back"            "OLD_BACKEND_MARKER" "$(head -1 "$R/app/backend/app/main.py")"
t "M1 old frontend content back"           "OLDBUILD" "$(head -1 "$R/app/frontend/.next/BUILD_ID")"
t "M1 old env preserved"                   "OLDENV" "$(head -1 "$R/app/backend/.env.lumin")"
t "M1 old frontend env preserved"          "OLDENVLOCAL" "$(head -1 "$R/app/frontend/.env.local")"
t "M1 persistent preserved"                "oldpersist" "$(head -1 "$R/app/backend/uploads/f.txt")"
t "M1 release-pair created (preserved)"    "1" "$(release_dirs "$R")"

# ══════════════════════════════════════════════════════════
# M2 — frontend start fails -> paired rollback
# ══════════════════════════════════════════════════════════
R="$BASE/m2"; build "$R"
FAKE_FAIL_START_FRONTEND=1 deploy "$R" >/dev/null 2>&1; rc=$?
t "M2 exit non-zero"                       "1" "$rc"
t "M2 pair restored (both present)"        "yes" "$(pair_ok "$R")"
t "M2 old backend content back"            "OLD_BACKEND_MARKER" "$(head -1 "$R/app/backend/app/main.py")"
t "M2 old frontend content back"           "OLDBUILD" "$(head -1 "$R/app/frontend/.next/BUILD_ID")"

# ══════════════════════════════════════════════════════════
# M3 — public health fails -> paired rollback
# ══════════════════════════════════════════════════════════
R="$BASE/m3"; build "$R"
FAKE_FAIL_PUBLIC=1 deploy "$R" >/dev/null 2>&1; rc=$?
t "M3 exit non-zero"                       "1" "$rc"
t "M3 pair restored (both present)"        "yes" "$(pair_ok "$R")"
t "M3 old backend content back"            "OLD_BACKEND_MARKER" "$(head -1 "$R/app/backend/app/main.py")"

# ══════════════════════════════════════════════════════════
# M4 — success path -> VERIFIED, new content live, old pair preserved
# ══════════════════════════════════════════════════════════
R="$BASE/m4"; build "$R"
deploy "$R" >/dev/null 2>&1; rc=$?
t "M4 exit 0"                              "0" "$rc"
t "M4 new backend live"                    "NEW_BACKEND_MARKER" "$(head -1 "$R/app/backend/app/main.py")"
t "M4 new frontend live"                   "S0kC8_NAlhQyCLKMFHHdQ" "$(head -1 "$R/app/frontend/.next/BUILD_ID")"
t "M4 persistent carried into new backend" "oldpersist" "$(head -1 "$R/app/backend/uploads/f.txt")"
t "M4 release-pair preserved"              "1" "$(release_dirs "$R")"
t "M4 old backend preserved in pair"       "yes" "$(find "$R/app/releases" -path '*/backend-old/app/main.py' -size +0c 2>/dev/null | head -1 | grep -q . && echo yes || echo no)"
t "M4 old frontend preserved in pair"      "yes" "$(find "$R/app/releases" -path '*/frontend-old/.next/BUILD_ID' -size +0c 2>/dev/null | head -1 | grep -q . && echo yes || echo no)"

# ══════════════════════════════════════════════════════════
# M5 — persistent content corrupted after switch -> rollback
# ══════════════════════════════════════════════════════════
R="$BASE/m5"; build "$R"
FAKE_CORRUPT_PERSIST=1 FAKE_PERSIST_FILE="$R/app/backend/uploads/f.txt" deploy "$R" >/dev/null 2>&1; rc=$?
# corruption is applied at service start (after activation), so the post-switch
# fingerprint check must detect it and roll back
t "M5 exit non-zero (fingerprint caught)"  "1" "$rc"
t "M5 pair restored (both present)"        "yes" "$(pair_ok "$R")"
t "M5 failed release preserved"            "1" "$(failed_dirs "$R")"

# ══════════════════════════════════════════════════════════
# M6 — rollback start failure -> non-zero, BOTH sides preserved
# ══════════════════════════════════════════════════════════
R="$BASE/m6"; build "$R"
# create a valid release-pair fixture to roll back to
P="$R/app/releases/release-pair-fixture"
mkdir -p "$P/backend-old/app" "$P/backend-old/.venv/bin" \
         "$P/frontend-old/.next/standalone" "$P/frontend-old/.next/static"
echo "PAIRBE" > "$P/backend-old/app/main.py"
printf '#!/bin/sh\nexit 0\n' > "$P/backend-old/.venv/bin/uvicorn"; chmod +x "$P/backend-old/.venv/bin/uvicorn"
echo "PAIRENV" > "$P/backend-old/.env.lumin"
echo "PAIRFE" > "$P/frontend-old/.next/standalone/server.js"
echo "PAIRBUILD" > "$P/frontend-old/.next/BUILD_ID"
( cd "$R/artifacts" && env PATH="$R/bin:$PATH" LUMIN_FIXTURE_ROOT="$R" FAKE_FAIL_START_BACKEND=1 \
    bash "$SCRIPTS/lumin-prod-rollback.sh" --execute --release-pair "$P" ) >/dev/null 2>&1; rc=$?
t "M6 rollback exit non-zero"              "1" "$rc"
t "M6 no partial state (backend present)"  "yes" "$([ -s "$R/app/backend/app/main.py" ] && echo yes || echo no)"
t "M6 no partial state (frontend present)" "yes" "$([ -s "$R/app/frontend/.next/BUILD_ID" ] && echo yes || echo no)"
t "M6 failed-rollback dir preserved"       "1" "$(failed_dirs "$R")"

echo ""
echo "=== RESULTS ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
echo "TOTAL: $((PASS+FAIL))"