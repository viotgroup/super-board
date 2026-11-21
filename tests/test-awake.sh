#!/usr/bin/env bash
# Tests scripts/super-board-awake.sh: a no-op without caffeinate, never kills a pid it
# did not start, and on macOS holds then releases one caffeinate.
#
#   bash tests/test-awake.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/super-board-awake.sh"
WORK="$(mktemp -d)"
export SB_AWAKE_PIDFILE="$WORK/state/inflight/awake.pid"
trap 'bash "$SCRIPT" stop >/dev/null 2>&1; rm -rf "$WORK"' EXIT

PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ❌ %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()  { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$2" "$3"; }

# 1 — usage errors exit 64.
RC=0; bash "$SCRIPT" >/dev/null 2>&1 || RC=$?; is "no verb → 64" 64 "$RC"
RC=0; bash "$SCRIPT" start soon >/dev/null 2>&1 || RC=$?
command -v caffeinate >/dev/null && is "non-numeric secs → 64" 64 "$RC" || is "non-numeric secs without caffeinate → 0" 0 "$RC"

# 2 — no caffeinate on PATH (Linux, CI): start is a no-op and writes nothing.
mkdir -p "$WORK/bin"; for c in bash git dirname basename mkdir; do ln -sf "$(command -v $c)" "$WORK/bin/$c"; done
RC=0; PATH="$WORK/bin" bash "$SCRIPT" start >/dev/null 2>&1 || RC=$?
is "start without caffeinate → 0" 0 "$RC"
[ -e "$SB_AWAKE_PIDFILE" ] && bad "no pid file without caffeinate" "absent" "present" || ok "no pid file without caffeinate"

# 3 — stop never kills a pid that is not a caffeinate, and clears the stale file.
sleep 30 & OTHER=$!
mkdir -p "$(dirname "$SB_AWAKE_PIDFILE")"; echo "$OTHER" > "$SB_AWAKE_PIDFILE"
bash "$SCRIPT" stop
kill -0 "$OTHER" 2>/dev/null && ok "a reused pid survives stop" || bad "a reused pid survives stop" "alive" "killed"
[ -e "$SB_AWAKE_PIDFILE" ] && bad "stop clears the pid file" "absent" "present" || ok "stop clears the pid file"
kill "$OTHER" 2>/dev/null; wait "$OTHER" 2>/dev/null

# 4 — macOS: start holds one caffeinate, a second start replaces it, stop ends it.
if command -v caffeinate >/dev/null; then
  bash "$SCRIPT" start 60; P1=$(cat "$SB_AWAKE_PIDFILE")
  ps -p "$P1" -o comm= | grep -q caffeinate && ok "start runs caffeinate" || bad "start runs caffeinate" caffeinate "$(ps -p "$P1" -o comm=)"
  bash "$SCRIPT" start 60; P2=$(cat "$SB_AWAKE_PIDFILE"); sleep 0.2
  kill -0 "$P1" 2>/dev/null && bad "a second start replaces the first" "first gone" "first alive" || ok "a second start replaces the first"
  bash "$SCRIPT" stop; sleep 0.2
  kill -0 "$P2" 2>/dev/null && bad "stop ends caffeinate" "gone" "alive" || ok "stop ends caffeinate"
else
  echo "  ⏭  caffeinate not installed: start/stop cycle skipped"
fi

echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" = 0 ]
