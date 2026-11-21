# viotgroup/super-board

VEEP's fork of [EricTechPro/super-board](https://github.com/EricTechPro/super-board). Branch
`veep` is upstream `main` plus the fixes our boards needed (first found on ECMS). Every
project repo installs from a `veep` tag, so a fix lands once here instead of drifting
between vendored copies.

## Rules

- Change the engine here, never in a project's `.claude/`. A re-install overwrites
  local edits.
- Every change ships with a test under `tests/`; `bash tests/run-safety.sh` must pass
  before a tag.
- A fix that is not VEEP-specific goes upstream as a PR too, so the fork stays thin.

## Install or upgrade a project

```bash
curl -fsSL https://raw.githubusercontent.com/viotgroup/super-board/veep/get.sh \
  | SUPER_BOARD_REPO=viotgroup/super-board SUPER_BOARD_REF=<tag> \
    bash -s -- --protect-main --no-helper-skills
```

Then commit the result in the project. Project files (`.claude/super-board/configs/`,
`docs/super-board/PROJECT.md`) are left alone.

## Take upstream changes

```bash
git fetch upstream
git switch veep && git merge upstream/main
bash tests/run-safety.sh
git tag v<upstream VERSION>-veep.<n> && git push origin veep --tags
```

Tags: `v3.3.0-veep.1`, `v3.3.0-veep.2`, … for fixes on the same upstream version; the
number resets when upstream `VERSION` moves.

## What `veep` adds over upstream 3.3.0

Ported from `viotgroup/ecms` (each commit names its ECMS source):

- GitHub reads: gh flag refusals, unknown `--json` fields, a PR/issue number that names
  nothing and an uncoercible `-F` value fail that call (exit 64), not the run.
- Merge gate: policy labels come from the issue the PR closes; DROPs in rollbacks, tests
  and QA evidence pass the schema gate; `DELETE FROM`/`TRUNCATE` in a forward migration
  gates it; optional `size_exclude_tests`.
- QA: evidence goes to the never-merged `super-board-evidence` branch, not the PR; QA
  tests the branch merged with the current base.
- Review: the truth-check runs as two workflow agents in fresh contexts, and is skipped
  on a re-review that will bounce anyway.
- Board: cards missing from `Issue.projectItems` are found on the board; human-merged
  cards close without a fresh review; a person can release a 🙋 card by moving it to Ready.
- Skills: QA's `testing-strategy` (no published source) falls back to a coverage table;
  skills a Claude plugin lists in `plugin.json` (nested, like `mattpocock-skills`) are found.
- Host: each wave checks `gh auth status` and keeps a macOS host awake.
- Lanes: each lane gets its own scratchpad folder; builders stamp migrations with
  `Date.now()`.
