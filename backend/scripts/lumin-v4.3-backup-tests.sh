#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-v4.3-backup-tests.sh
# REAL execute-mode backup test — proves checksum self-verify PASSES.
# Fixture + fake docker. Never touches production.
# ─────────────────────────────────────────────────────────
set -uo pipefail
SCRIPTS="/home/ubuntu/FaztrackAttendance/backend/scripts"
BASE="/tmp/lumin-backup-v43"
PASS=0; FAIL=0

t() {
  local name="$1" expect="$2" actual="$3"
  if [ "$expect" = "$actual" ]; then echo "PASS $name"; PASS=$((PASS+1))
  else echo "FAIL $name (expected=$expect got=$actual)"; FAIL=$((FAIL+1)); fi
}

build() {
  local root="$1"
  rm -rf "$root" 2>/dev/null
  mkdir -p "$root/bin" \
           "$root/app/backend/app" "$root/app/backend/.venv/bin" "$root/app/backend/uploads" \
           "$root/app/frontend/.next/standalone" "$root/app/frontend/.next/standalone/.next" "$root/app/frontend/.next/static" \
           "$root/backups"

  echo "BE" > "$root/app/backend/app/main.py"
  printf '#!/bin/sh\nexit 0\n' > "$root/app/backend/.venv/bin/uvicorn"
  chmod +x "$root/app/backend/.venv/bin/uvicorn"
  echo "ENVLUMIN" > "$root/app/backend/.env.lumin"
  echo "BUILDID" > "$root/app/frontend/.next/standalone/.next/BUILD_ID"
  echo "SRV" > "$root/app/frontend/.next/standalone/server.js"
  echo "ENVLOCAL" > "$root/app/frontend/.env.local"
  echo "persist" > "$root/app/backend/uploads/f.txt"

  # fake docker: emits content for pg_dump and Caddyfile
  cat > "$root/bin/docker" << 'EOF'
#!/bin/sh
args="$*"
case "$args" in
  *pg_dump*)    echo "FAKE_PG_DUMP_PAYLOAD" ; exit 0 ;;
  *pg_restore*) exit 0 ;;
  *Caddyfile*)  echo "test-caddyfile-content" ; exit 0 ;;
  *)            exit 0 ;;
esac
EOF
  chmod +x "$root/bin/docker"
}

BK="$BASE/fixture"
build "$BK"

echo "=== LUMIN V4.3 BACKUP EXECUTE TESTS ==="
echo "Fixture: $BK"
echo ""

RELEASE_ID="hotfix-test" LUMIN_FIXTURE_ROOT="$BK" PATH="$BK/bin:$PATH" \
  bash "$SCRIPTS/lumin-prod-backup.sh" --execute > /tmp/b43.log 2>&1; rc=$?

BDIR="$BK/backups/hotfix-test"

t "B1 execute backup exit 0"                "0" "$rc"
t "B2 backup dir created"                   "yes" "$([ -d "$BDIR" ] && echo yes || echo no)"
t "B3 checksums-sha256.txt exists"          "yes" "$([ -s "$BDIR/checksums-sha256.txt" ] && echo yes || echo no)"

# ── CORE HOTFIX PROOF: self-verify must PASS ──
if [ -d "$BDIR" ]; then
  ( cd "$BDIR" && sha256sum -c checksums-sha256.txt > /tmp/b43-verify.log 2>&1 ); vrc=$?
else
  vrc=99
fi
t "B4 sha256sum -c self-verify PASS"        "0" "$vrc"

t "B5 no .filelist.tmp in backup dir"       "0" "$(find "$BDIR" -name '.filelist.tmp' 2>/dev/null | wc -l | tr -d ' ')"
t "B6 manifest covered by checksums"        "yes" "$(grep -q '  BACKUP-MANIFEST.md$' "$BDIR/checksums-sha256.txt" 2>/dev/null && echo yes || echo no)"
t "B7 verification-result covered"          "yes" "$(grep -q '  VERIFICATION-RESULT.txt$' "$BDIR/checksums-sha256.txt" 2>/dev/null && echo yes || echo no)"
t "B8 VERIFICATION-RESULT is PASS"          "yes" "$(grep -q '^VERIFICATION-RESULT: PASS$' "$BDIR/VERIFICATION-RESULT.txt" 2>/dev/null && echo yes || echo no)"
t "B9 db dump present and non-empty"        "yes" "$(find "$BDIR" -maxdepth 1 -name 'db-dump-*.dump' -size +0c 2>/dev/null | head -1 | grep -q . && echo yes || echo no)"
t "B10 backend copied"                      "yes" "$([ -s "$BDIR/backend/app/main.py" ] && echo yes || echo no)"
t "B11 frontend copied"                     "yes" "$([ -s "$BDIR/frontend/.next/standalone/.next/BUILD_ID" ] && echo yes || echo no)"
t "B12 env files backed up"                 "yes" "$([ -s "$BDIR/backend.env.lumin" ] && [ -s "$BDIR/frontend.env.local" ] && echo yes || echo no)"
t "B13 persistent data backed up"           "yes" "$(find "$BDIR" -name f.txt -size +0c 2>/dev/null | head -1 | grep -q . && echo yes || echo no)"
t "B14 no leftover temp file anywhere"      "0" "$(find "$BDIR" -name '*.tmp' 2>/dev/null | wc -l | tr -d ' ')"

echo ""
echo "=== RESULTS ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
echo "TOTAL: $((PASS+FAIL))"
echo ""
echo "--- self-verify output (B4 evidence) ---"
head -5 /tmp/b43-verify.log 2>/dev/null