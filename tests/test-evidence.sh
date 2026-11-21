#!/usr/bin/env bash
# Tests scripts/super-board-evidence.sh against a local bare origin. No network.
# Evidence lands on its own branch, a re-run replaces its folder, the PR branch
# and the worktree are never touched, and the printed URL pins the commit.
#
#   bash tests/test-evidence.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/super-board-evidence.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  ✅ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ❌ %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()  { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$2" "$3"; }

g() { git -c user.email=t@t -c user.name=t "$@"; }
git init -q --bare "$WORK/origin.git"
g init -q -b issue-42-fix "$WORK/repo"
g -C "$WORK/repo" commit -q --allow-empty -m code
g -C "$WORK/repo" remote add origin "$WORK/origin.git"
g -C "$WORK/repo" push -q origin issue-42-fix
HEAD_BEFORE=$(git -C "$WORK/repo" rev-parse HEAD)

mkdir -p "$WORK/ev/sub"; echo png > "$WORK/ev/desktop.png"; echo log > "$WORK/ev/sub/run.log"
echo dirty > "$WORK/repo/untracked.txt"

# 1 — first publish creates the branch with the folder under its dest path.
OUT=$(cd "$WORK/repo" && GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t bash "$SCRIPT" "$WORK/ev" issue-42-qa-v1)
is "exit 0 and one JSON line" "issue-42-qa-v1" "$(printf '%s' "$OUT" | jq -r .path)"
SHA=$(printf '%s' "$OUT" | jq -r .sha)
is "branch tip is the printed commit" "$SHA" "$(git -C "$WORK/origin.git" rev-parse super-board-evidence)"
is "files land under the dest path" "issue-42-qa-v1/desktop.png
issue-42-qa-v1/sub/run.log" "$(git -C "$WORK/origin.git" ls-tree -r --name-only "$SHA")"
case "$(printf '%s' "$OUT" | jq -r .raw)" in */raw/"$SHA"/issue-42-qa-v1) ok "raw URL pins the commit" ;; *) bad "raw URL pins the commit" "*/raw/$SHA/issue-42-qa-v1" "$OUT" ;; esac

# 2 — the PR branch, its head and the dirty worktree are untouched.
is "issue branch head unchanged" "$HEAD_BEFORE" "$(git -C "$WORK/repo" rev-parse HEAD)"
is "remote issue branch unchanged" "$HEAD_BEFORE" "$(git -C "$WORK/origin.git" rev-parse issue-42-fix)"
is "worktree untouched" "dirty" "$(cat "$WORK/repo/untracked.txt")"
is "evidence shares no history with the code" "1" "$(git -C "$WORK/origin.git" rev-list --count "$SHA")"

# 3 — a re-run of the same QA version replaces its folder; other folders stay.
mkdir -p "$WORK/ev2"; echo png2 > "$WORK/ev2/mobile.png"
OUT2=$(cd "$WORK/repo" && GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t bash "$SCRIPT" "$WORK/ev2" issue-7-qa-v1)
OUT3=$(cd "$WORK/repo" && GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t bash "$SCRIPT" "$WORK/ev2" issue-42-qa-v1)
is "re-run replaces the folder, keeps the others" "issue-42-qa-v1/mobile.png
issue-7-qa-v1/mobile.png" "$(git -C "$WORK/origin.git" ls-tree -r --name-only "$(printf '%s' "$OUT3" | jq -r .sha)")"
is "commits stack on the evidence branch" "3" "$(git -C "$WORK/origin.git" rev-list --count super-board-evidence)"

# 4 — usage errors exit 64 and write nothing.
RC=0; (cd "$WORK/repo" && bash "$SCRIPT" "$WORK/nope" x) >/dev/null 2>&1 || RC=$?; is "missing folder → 64" 64 "$RC"
RC=0; (cd "$WORK/repo" && bash "$SCRIPT" "$WORK/ev" ../escape) >/dev/null 2>&1 || RC=$?; is "dest with .. → 64" 64 "$RC"
is "nothing written by refused calls" "3" "$(git -C "$WORK/origin.git" rev-list --count super-board-evidence)"

echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" = 0 ]
