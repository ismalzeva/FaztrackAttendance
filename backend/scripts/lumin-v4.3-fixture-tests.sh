#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# lumin-v4.1-fixture-tests.sh
# REAL fixture-based mutation/failure simulation
# Uses fake systemctl + curl + temporary fixture dirs
# NEVER touches production paths
# ─────────────────────────────────────────────────────────
set -uo pipefail
SCRIPTS="/home/ubuntu/FaztrackAttendance/backend/scripts"
BASE="/tmp/lumin-fixture-v41"
PASS=0; FAIL=0

t() {
  local name="$1" expect="$2" actual="$3"
  if [ "$expect" = "$actual" ]; then echo "PASS $name"; PASS=$((PASS+1))
  else echo "FAIL $name (expected=$expect got=$actual)"; FAIL=$((FAIL+1)); fi
}

# ── Fixture builder ──
build_fixture() {
  local root="$1"
  rm -rf "$root" 2>/dev/null
  mkdir -p "$root/bin" "$root/app/backend/app" "$root/app/backend/.venv/bin" \
           "$root/app/frontend/.next/standalone" "$root/app/frontend/.next/standalone/.next" "$root/app/frontend/.next/standalone/.next/static/chunks/app/admin" \
           "$root/app/frontend/.next/standalone/.next/static/chunks/app/dashboard" "$root/app/frontend/.next/standalone/.next/static/chunks/app/absen" \
           "$root/app/releases" "$root/backup" "$root/artifacts"

  # backend fixture
  echo "main" > "$root/app/backend/app/main.py"
  echo "#!/bin/sh" > "$root/app/backend/.venv/bin/uvicorn"; chmod +x "$root/app/backend/.venv/bin/uvicorn"
  echo "ENV" > "$root/app/backend/.env.lumin"
  # frontend fixture
  echo "S0kC8_NAlhQyCLKMFHHdQ" > "$root/app/frontend/.next/standalone/.next/BUILD_ID"
  echo "srv" > "$root/app/frontend/.next/standalone/server.js"
  echo "1" > "$root/app/frontend/.next/standalone/.next/static/chunks/app/admin/page-7ef835f4a59d5f3e.js"
  echo "2" > "$root/app/frontend/.next/standalone/.next/static/chunks/app/dashboard/page-18c48464202db5cb.js"
  echo "3" > "$root/app/frontend/.next/standalone/.next/static/chunks/app/absen/page-b864c4195106e108.js"
  echo "ENV" > "$root/app/frontend/.env.local"
  # persistent
  mkdir -p "$root/app/backend/uploads"; echo "u" > "$root/app/backend/uploads/f.txt"

  # ── fake systemctl: controllable ──
  cat > "$root/bin/systemctl" << 'EOF'
#!/bin/sh
# args: [start|stop|is-active] <svc>
act="$1"; svc="$2"
TOK=$(echo "$svc" | tr -c 'a-zA-Z0-9' '_')
# fail switches
[ -n "${FAKE_FAIL_START_BACKEND:-}" ] && [ "$act" = "start" ] && case "$svc" in *lumin.service) exit 1;; esac
[ -n "${FAKE_FAIL_START_FRONTEND:-}" ] && [ "$act" = "start" ] && case "$svc" in *lumin-web.service) exit 1;; esac
[ "$act" = "is-active" ] && { echo "active"; exit 0; }
exit 0
EOF
  chmod +x "$root/bin/systemctl"

  # ── fake sudo: pass-through ──
  cat > "$root/bin/sudo" << 'EOF'
#!/bin/sh
exec "$@"
EOF
  chmod +x "$root/bin/sudo"

  # ── fake curl: controllable health ──
  cat > "$root/bin/curl" << 'EOF'
#!/bin/sh
url=""
for a in "$@"; do case "$a" in http*) url="$a";; esac; done
[ -n "${FAKE_FAIL_PUBLIC:-}" ] && case "$url" in *gofaztrack.com*) exit 22;; esac
[ -n "${FAKE_FAIL_BACKEND:-}" ] && case "$url" in *:8011*) exit 22;; esac
echo "200"; exit 0
EOF
  chmod +x "$root/bin/curl"

  # ── fake docker (pg_restore --list) ──
  cat > "$root/bin/docker" << 'EOF'
#!/bin/sh
exit 0
EOF
  chmod +x "$root/bin/docker"
}

# ── Make artifacts + backup for deploy ──
make_artifacts() {
  local root="$1"
  # Build real tarballs with correct SHA? Cannot forge SHA — instead set env overrides.
  # We use fixture mode which does not skip SHA checks; so create dummy and override env.
  echo "dummy" > "$root/artifacts/lumin-backend-413720b1.tar.gz"
  echo "dummy" > "$root/artifacts/lumin-frontend-6e3a3e20.tar.gz"
}

make_backup() {
  local root="$1"
  local bk="$root/backup/exact-id"
  mkdir -p "$bk"
  cat > "$bk/BACKUP-MANIFEST.md" << EOF
# BACKUP MANIFEST
Hostname: $(hostname)
## Validation Status: PASS
EOF
  echo "x" > "$bk/db-dump-x.dump"
  printf 'VERIFICATION-RESULT: PASS\nverified_at: fixture\n' > "$bk/VERIFICATION-RESULT.txt"
  ( cd "$bk" && find . -type f -not -name "checksums-sha256.txt" -printf "%P\n" | sort | xargs sha256sum > checksums-sha256.txt )
}

echo "=== LUMIN V4.1 FIXTURE TESTS ==="
echo "Base: $BASE"
echo ""

# ══════════════════════════════════════════
# T1: DRY-RUN PRODUCES ZERO FILESYSTEM MUTATIONS
# ══════════════════════════════════════════
R="$BASE/t1"; build_fixture "$R"; make_artifacts "$R"; make_backup "$R"
BEFORE=$(find "$R" -type f | wc -l)
out=$(cd "$R/artifacts" && PATH="$R/bin:$PATH" LUMIN_FIXTURE_ROOT="$R" \
      bash "$SCRIPTS/lumin-prod-deploy.sh" --backup-dir "$R/backup/exact-id" 2>&1); rc=$?
AFTER=$(find "$R" -type f | wc -l)
t "T1 dry-run zero file mutations" "$BEFORE" "$AFTER"
t "T1b dry-run exit 0" "0" "$rc"

# ══════════════════════════════════════════
# T2: BACKUP DRY-RUN ZERO MUTATION (fake hostname/user/app)
# ══════════════════════════════════════════
R="$BASE/t2"; build_fixture "$R"
cat > "$R/bin/hostname" << 'EOF'
#!/bin/sh
echo "VM-8-230-ubuntu"
EOF
cat > "$R/bin/whoami" << 'EOF'
#!/bin/sh
echo "ubuntu"
EOF
chmod +x "$R/bin/hostname" "$R/bin/whoami"
BEFORE=$(find "$R" -type f | wc -l)
out=$(PATH="$R/bin:$PATH" LUMIN_FIXTURE_ROOT="$R" bash "$SCRIPTS/lumin-prod-backup.sh" 2>&1); rc=$?
AFTER=$(find "$R" -type f | wc -l)
t "T2 backup dry-run zero mutations" "$BEFORE" "$AFTER"

# ══════════════════════════════════════════
# T3: WRONG HOSTNAME → PREFLIGHT FATAL (no mutation)
# ══════════════════════════════════════════
R="$BASE/t3"; build_fixture "$R"
cat > "$R/bin/hostname" << 'EOF'
#!/bin/sh
echo "badhost"
EOF
chmod +x "$R/bin/hostname"
out=$(PATH="$R/bin:$PATH" bash "$SCRIPTS/lumin-prod-backup.sh" 2>&1); rc=$?
t "T3 wrong hostname exit1" "1" "$rc"
if echo "$out" | grep -q "Initiating paired rollback\|Manual recovery"; then
  t "T3b no rollback language in preflight" "0" "1"
else
  t "T3b no rollback language in preflight" "0" "0"
fi

# ══════════════════════════════════════════
# T4: MISSING BACKUP DIR → PREFLIGHT (no mutation lang)
# ══════════════════════════════════════════
R="$BASE/t4"; build_fixture "$R"; make_artifacts "$R"
out=$(cd "$R/artifacts" && PATH="$R/bin:$PATH" LUMIN_FIXTURE_ROOT="$R" \
      bash "$SCRIPTS/lumin-prod-deploy.sh" --backup-dir "$R/nope" 2>&1); rc=$?
t "T4 missing backup exit1" "1" "$rc"
if echo "$out" | grep -q "Initiating paired rollback\|Manual recovery"; then
  t "T4b preflight has no rollback language" "0" "1"
else
  t "T4b preflight has no rollback language" "0" "0"
fi

# ══════════════════════════════════════════
# T5: ROLLBACK DRY-RUN ZERO MUTATION
# ══════════════════════════════════════════
R="$BASE/t5"; build_fixture "$R"
PAIR="$R/app/releases/release-pair-20260101_000000"
mkdir -p "$PAIR/backend-old/app" "$PAIR/backend-old/.venv/bin" "$PAIR/frontend-old/.next/standalone/.next/static"
echo main > "$PAIR/backend-old/app/main.py"
echo uvicorn > "$PAIR/backend-old/.venv/bin/uvicorn"
echo env > "$PAIR/backend-old/.env.lumin"
echo srv > "$PAIR/frontend-old/.next/standalone/server.js"
echo bid > "$PAIR/frontend-old/.next/standalone/.next/BUILD_ID"
BEFORE=$(find "$R" -type f | wc -l)
out=$(PATH="$R/bin:$PATH" LUMIN_FIXTURE_ROOT="$R" bash "$SCRIPTS/lumin-prod-rollback.sh" --release-pair "$PAIR" 2>&1); rc=$?
AFTER=$(find "$R" -type f | wc -l)
t "T5 rollback dry-run zero mutations" "$BEFORE" "$AFTER"
t "T5b rollback dry-run exit 0" "0" "$rc"

# ══════════════════════════════════════════
# T6: ROLLBACK PATH TRAVERSAL REJECTED
# ══════════════════════════════════════════
R="$BASE/t6"; build_fixture "$R"
mkdir -p "$R/outside/backend-old/app" "$R/outside/frontend-old/.next"
out=$(PATH="$R/bin:$PATH" LUMIN_FIXTURE_ROOT="$R" bash "$SCRIPTS/lumin-prod-rollback.sh" --release-pair "$R/outside" 2>&1); rc=$?
t "T6 path outside releases/ rejected" "1" "$rc"

# ══════════════════════════════════════════
# T7: ROLLBACK PARTIAL PAIR (frontend-old missing)
# ══════════════════════════════════════════
R="$BASE/t7"; build_fixture "$R"
P="$R/app/releases/release-pair-20260101_000001"
mkdir -p "$P/backend-old/app"
out=$(PATH="$R/bin:$PATH" LUMIN_FIXTURE_ROOT="$R" bash "$SCRIPTS/lumin-prod-rollback.sh" --release-pair "$P" 2>&1); rc=$?
t "T7 partial pair rejected" "1" "$rc"

# ══════════════════════════════════════════
# T8: BACKUP WITHOUT VERIFICATION-RESULT -> FATAL
# ══════════════════════════════════════════
R="$BASE/t8"; build_fixture "$R"; make_artifacts "$R"; make_backup "$R"
rm -f "$R/backup/exact-id/VERIFICATION-RESULT.txt"
( cd "$R/backup/exact-id" && find . -type f -not -name "checksums-sha256.txt" -printf "%P\n" | sort | xargs sha256sum > checksums-sha256.txt )
out=$(cd "$R/artifacts" && PATH="$R/bin:$PATH" LUMIN_FIXTURE_ROOT="$R" \
      bash "$SCRIPTS/lumin-prod-deploy.sh" --backup-dir "$R/backup/exact-id" 2>&1); rc=$?
t "T8 missing VERIFICATION-RESULT -> exit1" "1" "$rc"

# ══════════════════════════════════════════
# T9: BACKUP WITH VERIFICATION-RESULT: FAIL -> FATAL
# ══════════════════════════════════════════
R="$BASE/t9"; build_fixture "$R"; make_artifacts "$R"; make_backup "$R"
printf 'VERIFICATION-RESULT: FAIL\n' > "$R/backup/exact-id/VERIFICATION-RESULT.txt"
( cd "$R/backup/exact-id" && find . -type f -not -name "checksums-sha256.txt" -printf "%P\n" | sort | xargs sha256sum > checksums-sha256.txt )
out=$(cd "$R/artifacts" && PATH="$R/bin:$PATH" LUMIN_FIXTURE_ROOT="$R" \
      bash "$SCRIPTS/lumin-prod-deploy.sh" --backup-dir "$R/backup/exact-id" 2>&1); rc=$?
t "T9 VERIFICATION-RESULT:FAIL -> exit1" "1" "$rc"

echo ""
echo "=== RESULTS ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
echo "TOTAL: $((PASS+FAIL))"