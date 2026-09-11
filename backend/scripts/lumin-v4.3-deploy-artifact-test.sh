#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-v4.3-deploy-artifact-test.sh
# REAL deploy pipeline test using the ACTUAL production artifacts:
#   backend : lumin-backend-413720b1.tar.gz  SHA b8b5d890f2e0d6dfdf225740e561b9c6b2ef6854f0416c96e283936ef334bba5
#   frontend: lumin-frontend-6e3a3e20.tar.gz SHA f96bf8805f19b555fb6f3509e57c3bc241f3cbeaa0a54b175a90f1fc71ec5740
# Fixture + fake systemctl/curl/docker. Never touches production.
# ─────────────────────────────────────────────────────────
set -uo pipefail
SCRIPTS="/home/ubuntu/FaztrackAttendance/backend/scripts"
SRC_ART="/tmp/lumin-release-20260909"
ART_BE="$SRC_ART/lumin-backend-413720b1.tar.gz"
ART_FE="$SRC_ART/lumin-frontend-6e3a3e20.tar.gz"
EXP_BE_SHA="b8b5d890f2e0d6dfdf225740e561b9c6b2ef6854f0416c96e283936ef334bba5"
EXP_FE_SHA="f96bf8805f19b555fb6f3509e57c3bc241f3cbeaa0a54b175a90f1fc71ec5740"
EXP_BUILD="S0kC8_NAlhQyCLKMFHHdQ"
BASE="/tmp/lumin-artifact-test"
PASS=0; FAIL=0

t() {
  local name="$1" expect="$2" actual="$3"
  if [ "$expect" = "$actual" ]; then echo "PASS $name"; PASS=$((PASS+1))
  else echo "FAIL $name (expected=$expect got=$actual)"; FAIL=$((FAIL+1)); fi
}

echo "=== LUMIN V4.3 DEPLOY-ARTIFACT TEST (real production artifacts) ==="
echo ""

# ── A1/A2: the real artifacts must be present with the production SHA ──
if [ ! -f "$ART_BE" ] || [ ! -f "$ART_FE" ]; then
  echo "FATAL: production artifacts not found under $SRC_ART"
  exit 1
fi
t "A1 backend artifact SHA == b8b5d890..." "0" "$([ "$(sha256sum "$ART_BE" | awk '{print $1}')" = "$EXP_BE_SHA" ] && echo 0 || echo 1)"
t "A2 frontend artifact SHA == f96bf880..." "0" "$([ "$(sha256sum "$ART_FE" | awk '{print $1}')" = "$EXP_FE_SHA" ] && echo 0 || echo 1)"

# ── A3: artifact layout must be <prefix>/backend/... (documents the fix) ──
t "A3 backend artifact nesting = lumin-backend/backend/app/main.py" "lumin-backend/backend/app/main.py" \
  "$(tar tzf "$ART_BE" | grep -m1 'app/main.py$')"

# ── build fixture around the REAL artifacts ──
R="$BASE/fix"
rm -rf "$R" 2>/dev/null
mkdir -p "$R/bin" "$R/artifacts" \
         "$R/app/backend/app" "$R/app/backend/.venv/bin" "$R/app/backend/uploads" \
         "$R/app/frontend/.next/standalone" "$R/app/frontend/.next/standalone/.next" \
         "$R/app/frontend/.next/standalone/.next/static/chunks/app/admin" \
         "$R/app/frontend/.next/standalone/.next/static/chunks/app/dashboard" \
         "$R/app/frontend/.next/standalone/.next/static/chunks/app/absen" \
         "$R/app/releases" "$R/backup/exact-id"

# current OLD release (what production looks like today)
echo "OLD_BACKEND_MARKER" > "$R/app/backend/app/main.py"
printf '#!/bin/sh\nexit 0\n' > "$R/app/backend/.venv/bin/uvicorn"; chmod +x "$R/app/backend/.venv/bin/uvicorn"
echo "OLDENV" > "$R/app/backend/.env.lumin"
echo "OLDBUILD" > "$R/app/frontend/.next/standalone/.next/BUILD_ID"
echo "OLDSRV" > "$R/app/frontend/.next/standalone/server.js"
echo "OLDADMIN" > "$R/app/frontend/.next/standalone/.next/static/chunks/app/admin/page-oldadmin.js"
echo "OLDDASH"  > "$R/app/frontend/.next/standalone/.next/static/chunks/app/dashboard/page-olddash.js"
echo "OLDABSEN" > "$R/app/frontend/.next/standalone/.next/static/chunks/app/absen/page-oldabsen.js"
echo "OLDENVLOCAL" > "$R/app/frontend/.env.local"
echo "oldpersist" > "$R/app/backend/uploads/f.txt"

# the REAL production artifacts, under their exact filenames
cp "$ART_BE" "$R/artifacts/lumin-backend-413720b1.tar.gz"
cp "$ART_FE" "$R/artifacts/lumin-frontend-6e3a3e20.tar.gz"

# fake systemctl / sudo / curl / docker
cat > "$R/bin/systemctl" << 'EOF'
#!/bin/sh
act="$1"; svc="$2"
[ -n "${FAKE_FAIL_START_BACKEND:-}" ] && [ "$act" = "start" ] && case "$svc" in *lumin.service) exit 1;; esac
[ -n "${FAKE_FAIL_START_FRONTEND:-}" ] && [ "$act" = "start" ] && case "$svc" in *lumin-web.service) exit 1;; esac
[ "$act" = "is-active" ] && { echo "active"; exit 0; }
exit 0
EOF
chmod +x "$R/bin/systemctl"
printf '#!/bin/sh\nexec "$@"\n' > "$R/bin/sudo"; chmod +x "$R/bin/sudo"
cat > "$R/bin/curl" << 'EOF'
#!/bin/sh
echo "200"; exit 0
EOF
chmod +x "$R/bin/curl"
printf '#!/bin/sh\nexit 0\n' > "$R/bin/docker"; chmod +x "$R/bin/docker"

# backup fixture
cat > "$R/backup/exact-id/BACKUP-MANIFEST.md" << EOF
# BACKUP MANIFEST
Hostname: $(hostname)
## Validation Status: PASS
EOF
echo "dump" > "$R/backup/exact-id/db-dump-x.dump"
printf 'VERIFICATION-RESULT: PASS\nverified_at: fixture\n' > "$R/backup/exact-id/VERIFICATION-RESULT.txt"
( cd "$R/backup/exact-id" && find . -type f -not -name "checksums-sha256.txt" -printf "%P\n" | LC_ALL=C sort | xargs sha256sum > checksums-sha256.txt )

# ── A4: run the real pipeline ──
( cd "$R/artifacts" && env PATH="$R/bin:$PATH" LUMIN_FIXTURE_ROOT="$R" \
    bash "$SCRIPTS/lumin-prod-deploy.sh" --execute --backup-dir "$R/backup/exact-id" ) \
    > /tmp/art-deploy.log 2>&1; rc=$?
t "A4 deploy exit 0 (real artifacts)" "0" "$rc"

# ── A5..A9: post-conditions proving correct extraction ──
t "A5 app/main.py present after switch"      "yes" "$([ -s "$R/app/backend/app/main.py" ] && echo yes || echo no)"
t "A6 app/main.py is REAL artifact content"  "yes" "$(grep -q 'FastAPI' "$R/app/backend/app/main.py" 2>/dev/null && echo yes || echo no)"
t "A7 scripts/ extracted alongside app/"    "yes" "$([ -d "$R/app/backend/scripts" ] && echo yes || echo no)"
t "A8 NO double nesting backend/backend"     "0" "$([ -d "$R/app/backend/backend" ] && echo 1 || echo 0)"
t "A9 NO double nesting app/app"             "0" "$([ -d "$R/app/backend/app/app" ] && echo 1 || echo 0)"
t "A10 frontend BUILD_ID live"               "$EXP_BUILD" "$(cat "$R/app/frontend/.next/standalone/.next/BUILD_ID" 2>/dev/null)"
t "A11 frontend server.js present"           "yes" "$([ -s "$R/app/frontend/.next/standalone/server.js" ] && echo yes || echo no)"
t "A12 .venv preserved"                      "yes" "$([ -x "$R/app/backend/.venv/bin/uvicorn" ] && echo yes || echo no)"
t "A13 .env.lumin preserved"                 "OLDENV" "$(cat "$R/app/backend/.env.lumin" 2>/dev/null)"
t "A14 persistent data carried"              "oldpersist" "$(cat "$R/app/backend/uploads/f.txt" 2>/dev/null)"
t "A15 release-pair preserved"               "1" "$(find "$R/app/releases" -maxdepth 1 -type d -name 'release-pair-*' 2>/dev/null | wc -l | tr -d ' ')"

# ── A16: regression — a failure after switch still rolls back cleanly ──
R2="$BASE/fix2"
cp -a "$R" "$R2" 2>/dev/null
# rebuild a pristine fixture for the failure case
rm -rf "$R2" 2>/dev/null
mkdir -p "$R2/bin" "$R2/artifacts" "$R2/app/backend/app" "$R2/app/backend/.venv/bin" \
         "$R2/app/backend/uploads" "$R2/app/frontend/.next/standalone" "$R2/app/frontend/.next/standalone/.next" \
         "$R2/app/frontend/.next/standalone/.next/static/chunks/app/admin" "$R2/app/frontend/.next/standalone/.next/static/chunks/app/dashboard" \
         "$R2/app/frontend/.next/standalone/.next/static/chunks/app/absen" "$R2/app/releases" "$R2/backup/exact-id"
echo "OLD_BACKEND_MARKER" > "$R2/app/backend/app/main.py"
printf '#!/bin/sh\nexit 0\n' > "$R2/app/backend/.venv/bin/uvicorn"; chmod +x "$R2/app/backend/.venv/bin/uvicorn"
echo "OLDENV" > "$R2/app/backend/.env.lumin"
echo "OLDBUILD" > "$R2/app/frontend/.next/standalone/.next/BUILD_ID"
echo "OLDSRV" > "$R2/app/frontend/.next/standalone/server.js"
echo "OLDADMIN" > "$R2/app/frontend/.next/standalone/.next/static/chunks/app/admin/page-oldadmin.js"
echo "OLDDASH"  > "$R2/app/frontend/.next/standalone/.next/static/chunks/app/dashboard/page-olddash.js"
echo "OLDABSEN" > "$R2/app/frontend/.next/standalone/.next/static/chunks/app/absen/page-oldabsen.js"
echo "OLDENVLOCAL" > "$R2/app/frontend/.env.local"
echo "oldpersist" > "$R2/app/backend/uploads/f.txt"
cp "$ART_BE" "$R2/artifacts/lumin-backend-413720b1.tar.gz"
cp "$ART_FE" "$R2/artifacts/lumin-frontend-6e3a3e20.tar.gz"
cp -a "$R/bin/." "$R2/bin/"
cat > "$R2/backup/exact-id/BACKUP-MANIFEST.md" << EOF
# BACKUP MANIFEST
Hostname: $(hostname)
## Validation Status: PASS
EOF
echo "dump" > "$R2/backup/exact-id/db-dump-x.dump"
printf 'VERIFICATION-RESULT: PASS\n' > "$R2/backup/exact-id/VERIFICATION-RESULT.txt"
( cd "$R2/backup/exact-id" && find . -type f -not -name "checksums-sha256.txt" -printf "%P\n" | LC_ALL=C sort | xargs sha256sum > checksums-sha256.txt )

( cd "$R2/artifacts" && env PATH="$R2/bin:$PATH" LUMIN_FIXTURE_ROOT="$R2" FAKE_FAIL_START_FRONTEND=1 \
    bash "$SCRIPTS/lumin-prod-deploy.sh" --execute --backup-dir "$R2/backup/exact-id" ) \
    > /tmp/art-deploy-fail.log 2>&1; rc2=$?
t "A16 forced failure exits non-zero"        "1" "$rc2"
t "A17 old pair restored on failure"         "yes" "$([ -s "$R2/app/backend/app/main.py" ] && [ -s "$R2/app/frontend/.next/standalone/.next/BUILD_ID" ] && echo yes || echo no)"
t "A18 old backend marker restored"          "OLD_BACKEND_MARKER" "$(cat "$R2/app/backend/app/main.py" 2>/dev/null)"

echo ""
echo "=== RESULTS ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
echo "TOTAL: $((PASS+FAIL))"