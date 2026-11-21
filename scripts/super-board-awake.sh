#!/usr/bin/env bash
# super-board-awake.sh — keep a macOS host awake while a board run is live.
#
# WHY. On 2026-10-24 (ECMS run 2, wave 2) the laptop slept mid-wave: both lanes came back
# "agent returned no result" and the login had expired by the time anyone looked. Lanes run on
# this machine, so an idle sleep ends every one of them.
#
# HOW. `start` (re)starts one `caffeinate -i -t <secs>` for this repo and keeps its pid in
# .claude/super-board/inflight/awake.pid. The orchestrator calls it before each wave, so a run
# that dies stops holding the machine within <secs>. `stop` ends it at run end or on stop.
# `-i` blocks idle sleep only: closing the lid on battery still sleeps the machine.
# Off macOS (no caffeinate) both verbs are a no-op, exit 0.
#
# Usage: super-board-awake.sh start [secs]   default 14400 (4h, longer than any wave so far)
#        super-board-awake.sh stop
set -euo pipefail

# Same state rule as super-board-card.sh: <main checkout>/.claude/super-board for a plain
# repo and every worktree, <common dir>/super-board inside a submodule.
state_dir() {
  local common
  common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
  if [ -z "$common" ]; then echo "$PWD/.claude/super-board"
  elif [ "$(basename "$common")" = .git ]; then echo "$(dirname "$common")/.claude/super-board"
  else echo "$common/super-board"; fi
}
PIDFILE="${SB_AWAKE_PIDFILE:-$(state_dir)/inflight/awake.pid}"

stop() {
  [ -f "$PIDFILE" ] || return 0
  pid=$(cat "$PIDFILE")
  # Only kill what we started: a reused pid must still be a caffeinate.
  if ps -p "$pid" -o comm= 2>/dev/null | grep -q caffeinate; then kill "$pid" 2>/dev/null || true; fi
  rm -f "$PIDFILE"
}

case "${1:-}" in
  start)
    command -v caffeinate >/dev/null || exit 0
    secs=${2:-14400}
    [[ "$secs" =~ ^[0-9]+$ ]] || { echo "super-board-awake.sh: secs must be a number" >&2; exit 64; }
    stop
    mkdir -p "$(dirname "$PIDFILE")"
    nohup caffeinate -i -t "$secs" >/dev/null 2>&1 &
    echo $! > "$PIDFILE"
    ;;
  stop) stop ;;
  *) echo "usage: super-board-awake.sh start [secs] | stop" >&2; exit 64 ;;
esac
