#!/usr/bin/env bash
# super-board-evidence.sh — publish a QA evidence folder without touching the PR branch.
#
# WHY. QA used to commit screenshots, logs and REPORT.md to the issue branch so the raw
# URLs in its comments would render. That put 130–160 non-code lines into every PR diff
# (#123 and #129 on 2026-10-24) and pushed PRs over merge_policy.auto_max_lines, so the
# owner had to merge them by hand. Evidence now goes to its own branch, which is never
# merged and shares no history with the code (`super-board-evidence`). Comments embed raw
# URLs pinned to the commit this script prints, so they keep rendering after the issue
# branch is merged and deleted.
#
# HOW. Nothing is checked out: the commit is built in a temporary index on top of the
# evidence branch tip, so it is safe to run from any worktree, dirty or not. A push that
# loses the race to another lane is rebuilt on the new tip and retried.
#
# Usage:
#   super-board-evidence.sh <dir> <dest-path>
#     <dir>        local evidence folder, kept OUTSIDE the worktree
#     <dest-path>  folder inside the evidence branch, e.g. issue-42-qa-v1
#
# Stdout, one JSON line:
#   {"sha":"<commit>","path":"issue-42-qa-v1","raw":"https://github.com/<o>/<r>/raw/<commit>/issue-42-qa-v1"}
# Append a file name to `raw` for an image URL. Exit 0 ok · 64 usage · 1 push failed.
set -euo pipefail

BRANCH="${SB_EVIDENCE_BRANCH:-super-board-evidence}"
DIR="${1:-}"; DEST="${2:-}"
[ -n "$DIR" ] && [ -n "$DEST" ] || { echo "usage: super-board-evidence.sh <dir> <dest-path>" >&2; exit 64; }
[ -d "$DIR" ] || { echo "not a directory: $DIR" >&2; exit 64; }
DEST="${DEST#/}"; DEST="${DEST%/}"
case "$DEST" in ''|*..*) echo "bad dest-path: $DEST" >&2; exit 64 ;; esac

# owner/name from any remote form, SSH host aliases included (git@github.com-veep:o/r.git).
SLUG=$(git remote get-url origin | sed -E 's#\.git$##; s#^.*[:/]([^/:]+/[^/:]+)$#\1#')
IDX=$(mktemp); trap 'rm -f "$IDX"' EXIT

for attempt in 1 2 3 4 5; do
  PARENT=""
  if git fetch -q origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" 2>/dev/null; then
    PARENT=$(git rev-parse "refs/remotes/origin/$BRANCH")
  fi
  rm -f "$IDX"
  if [ -n "$PARENT" ]; then GIT_INDEX_FILE="$IDX" git read-tree "$PARENT"
  else GIT_INDEX_FILE="$IDX" git read-tree --empty; fi
  # A re-run of the same QA version replaces its folder instead of mixing old files in.
  GIT_INDEX_FILE="$IDX" git rm -r -q --cached --ignore-unmatch -- "$DEST" >/dev/null
  while IFS= read -r -d '' f; do
    f="${f#./}"
    blob=$(git hash-object -w -- "$DIR/$f")
    GIT_INDEX_FILE="$IDX" git update-index --add --cacheinfo "100644,$blob,$DEST/$f"
  done < <(cd "$DIR" && find . -type f -print0)
  TREE=$(GIT_INDEX_FILE="$IDX" git write-tree)
  if [ -n "$PARENT" ]; then COMMIT=$(git commit-tree "$TREE" -p "$PARENT" -m "evidence: $DEST")
  else COMMIT=$(git commit-tree "$TREE" -m "evidence: $DEST"); fi
  if git push -q origin "$COMMIT:refs/heads/$BRANCH" 2>/dev/null; then
    printf '{"sha":"%s","path":"%s","raw":"https://github.com/%s/raw/%s/%s"}\n' \
      "$COMMIT" "$DEST" "$SLUG" "$COMMIT" "$DEST"
    exit 0
  fi
  sleep $((attempt * 2))   # another lane pushed first: rebuild on its tip
done
echo "could not push evidence to $BRANCH after 5 attempts" >&2
exit 1
