#!/usr/bin/env python3
"""super-board-merge-policy.py — decide who merges a PR and which migrations run.

Called by super-board-merge-gate.sh inside the merge lock. Pure function of the
config, the PR metadata and the diff; no network, no side effects.

    super-board-merge-policy.py --config <cfg.json> --meta <pr.json> --diff <file>

`--meta` is `gh pr view --json labels,files,additions,deletions,body`.
`--diff` is `gh pr diff` (only ADDED lines are searched for keywords; in a
migration file, lines inside a `down()`/`downgrade()` rollback are skipped).

Stdout, one JSON object:
    { "human":      [ {"category": "money", "why": "path src/billing/charge.ts"} ],
      "migrations": [ "supabase/migrations/2026_add_col.sql" ],
      "run":        [ {"env": "staging", "cmd": "npm run db:migrate:staging"} ],
      "needs_you":  [ "npm run db:migrate:live   # live: not in allowed_envs" ],
      "done":       false }

`human` non-empty → the gate exits 7 (a human merges). `needs_you` non-empty →
exit 8 unless the gate verifies a head-bound approval. `done` stays false for
compatibility: labels never supply authority. Exit 2 on
unreadable metadata, so the gate fails safe to "human".

Rules, from references/config-schema.json → merge_policy / migrations:
- merge_policy.default "human" → everything waits for a human.
- config `host` other than "claude" (codex, opencode) with merge_policy.default
  unset → everything waits for a human; an explicit "auto" merges as usual.
  Read from config only (no host detection). No `host` key → the Claude
  default ("auto"), so configs written before the key existed do not change.
- merge_policy.auto_max_lines (default 400; 0 = no cap): changed lines
  (additions + deletions) above it → human ("big PR — please review"). Files
  matching merge_policy.size_exclude (default: lockfiles, generated output,
  snapshots, QA evidence under docs/super-board/runs/) and migration files (migrations.globs) do not count; migration
  lines are reported separately. Per-file counts come from `files[].additions/
  deletions`; without them the PR totals are used.
- merge_policy.always_human.<category>: any label, path glob or added-line
  keyword match → human. Categories ship as money, auth, schema; setting
  always_human replaces them all (`{}` turns category gating off). In migration
  files, keywords inside a `down()`/`downgrade()` rollback are ignored, and
  `delete from`/`truncate` outside it → schema (while the schema category is on).
  Schema keywords are not searched in test files (TEST_PATHS).
- migrations: a changed path matching migrations.globs makes this a migration
  PR. Every env in allowed_envs with a command runs (a command of "-" means the
  deploy pipeline applies it: nothing to run). target_env not in allowed_envs, or
  allowed with no command → needs you. migrations.human_steps (any migration PR)
  and `needs-you: <command>` lines in the PR body, bare or inside the status alert (any PR) → needs you.
Globs: `**/` any directories (incl. none), `**` anything, `*` within one path
segment, `?` one character.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

DEFAULT_ALWAYS_HUMAN = {
    "money": {
        "labels": ["money", "payments", "billing", "pricing"],
        "paths": ["**/billing/**", "**/payments/**", "**/stripe/**", "**/checkout/**"],
        "keywords": ["stripe.", "payment_intent", "price_id", "invoice.create"],
    },
    "auth": {
        "labels": ["auth", "security"],
        "paths": ["**/auth/**", "**/authz/**", "**/permissions/**"],
        "keywords": ["jwt.sign", "bcrypt", "password_hash", "service_role"],
    },
    "schema": {
        "labels": ["schema", "destructive"],
        "paths": [],
        "keywords": ["drop table", "drop column", "truncate table", "rename column", "drop schema"],
    },
}
DEFAULT_MIGRATION_GLOBS = [
    "supabase/migrations/**", "prisma/migrations/**", "drizzle/**",
    "**/migrations/*.sql", "db/migrate/**", "alembic/versions/**",
]
DEFAULT_ALLOWED_ENVS = ["test", "staging"]
DEFAULT_AUTO_MAX_LINES = 400
DEFAULT_SIZE_EXCLUDE = [
    # lockfiles
    "**/package-lock.json", "**/yarn.lock", "**/pnpm-lock.yaml", "**/bun.lockb", "**/bun.lock",
    "**/npm-shrinkwrap.json", "**/Cargo.lock", "**/poetry.lock", "**/uv.lock", "**/Pipfile.lock",
    "**/Gemfile.lock", "**/composer.lock", "**/go.sum", "**/Podfile.lock", "**/pubspec.lock",
    # generated output
    "**/generated/**", "**/__generated__/**", "**/*.generated.*", "**/*.gen.*",
    "**/*.min.js", "**/*.min.css", "**/*.map", "**/*.pb.go", "**/*_pb2.py",
    # snapshots
    "**/__snapshots__/**", "**/*.snap",
    # QA evidence the pipeline itself commits (reports, logs, screenshots)
    "docs/super-board/runs/**",
]
# Test code never runs against a real database, so its DROP/TRUNCATE teardown is
# not a destructive change; these paths are skipped by the schema keyword scan.
TEST_PATHS = [
    "**/*.spec.*", "**/*.test.*", "**/*-spec.*", "**/test/**", "**/tests/**",
    "**/e2e/**", "**/__tests__/**", "test/**", "tests/**", "e2e/**",
]


def glob_re(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"; i += 3
        elif pattern.startswith("**", i):
            out += ".*"; i += 2
        elif pattern[i] == "*":
            out += "[^/]*"; i += 1
        elif pattern[i] == "?":
            out += "[^/]"; i += 1
        else:
            out += re.escape(pattern[i]); i += 1
    return re.compile("^" + out + "$")


def matches(path: str, globs) -> str | None:
    for g in globs or []:
        if glob_re(g).match(path):
            return g
    return None


# Rollback bodies: `down(` (TypeORM, Knex) and `downgrade(` (Alembic) undo an
# additive `up()` with DROP lines, which is not a destructive change.
# Only method declarations match, so a comment mentioning down() inside up()
# cannot hide the rest of up().
_DECL = r"^\s*(?:(?:public|private|protected|async|static|def|exports\.)\s*)*"
DOWN_RE = re.compile(_DECL + r"(?:down|downgrade)\s*(?:=\s*(?:async\s*)?(?:function\s*)?)?\(")
UP_RE = re.compile(_DECL + r"(?:up|upgrade)\s*(?:=\s*(?:async\s*)?(?:function\s*)?)?\(")


# Data loss a forward migration can do without any DROP keyword. Searched only in
# migration files, outside rollback methods, so app code that deletes rows does
# not trip the schema gate.
MIGRATION_DATA_LOSS = ["delete from", "truncate "]


def keyword_lines(diff: str, mig_files) -> tuple[str, str, str]:
    """Added lines to search for keywords, lowercased:
    (all files, all files but tests, migrations only).

    Inside a migration file, lines in a rollback method are skipped. The method
    is tracked from `up(`/`down(` lines and from git's hunk-header function
    context; a hunk with no known method is searched (fail safe to "human").
    """
    out, app_out, mig_out, path, in_down = [], [], [], None, False
    for l in diff.splitlines():
        if l.startswith("+++"):
            path = l[6:] if l.startswith("+++ b/") else None
            in_down = False
            continue
        if l.startswith("---"):
            continue
        is_mig = path in mig_files
        if l.startswith("@@"):
            ctx = l.split("@@", 2)[-1].lstrip(" ")
            in_down = bool(is_mig and DOWN_RE.search(ctx) and not UP_RE.search(ctx))
            continue
        if is_mig and not l.startswith("-"):
            if DOWN_RE.search(l[1:]):
                in_down = True
            elif UP_RE.search(l[1:]):
                in_down = False
        if l.startswith("+") and not (is_mig and in_down):
            out.append(l[1:])
            if is_mig:
                mig_out.append(l[1:])
            if is_mig or not matches(path or "", TEST_PATHS):
                app_out.append(l[1:])
    return tuple("\n".join(x).lower() for x in (out, app_out, mig_out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--diff", required=True)
    a = ap.parse_args()

    cfg = json.load(open(a.config))
    try:
        meta = json.load(open(a.meta))
    except ValueError:
        return 2
    if not isinstance(meta, dict) or "files" not in meta:
        return 2
    diff = open(a.diff, errors="replace").read()

    labels = {(l.get("name") or "").lower() for l in meta.get("labels") or []}
    paths = [f.get("path") or "" for f in meta.get("files") or []]
    body = meta.get("body") or ""

    policy = cfg.get("merge_policy") or {}
    human = []
    host = cfg.get("host")
    if (policy.get("default") or "auto") == "human":
        human.append({"category": "default", "why": "merge_policy.default is \"human\""})
    elif not policy.get("default") and host not in (None, "claude"):
        human.append({"category": "default", "why": f"host {host}: auto-merge not turned on "
                      "(set merge_policy.default to \"auto\")"})
    mig = cfg.get("migrations") or {}
    globs = mig.get("globs") or DEFAULT_MIGRATION_GLOBS
    mig_files = [p for p in paths if matches(p, globs)]
    added, app_added, mig_added = keyword_lines(diff, mig_files)

    cap = policy.get("auto_max_lines")
    cap = DEFAULT_AUTO_MAX_LINES if cap is None else int(cap)
    excl = policy.get("size_exclude")
    excl = DEFAULT_SIZE_EXCLUDE if excl is None else excl
    files = meta.get("files") or []
    if files and all("additions" in f for f in files):
        size = mig_lines = 0
        for f in files:
            n = int(f.get("additions") or 0) + int(f.get("deletions") or 0)
            p = f.get("path") or ""
            if p in mig_files:
                mig_lines += n
            elif not matches(p, excl):
                size += n
    else:
        size, mig_lines = int(meta.get("additions") or 0) + int(meta.get("deletions") or 0), 0
    if cap > 0 and size > cap:
        extra = f" (+{mig_lines} migration lines counted separately)" if mig_lines else ""
        human.append({"category": "size",
                      "why": f"big PR — please review: {size} changed lines > auto_max_lines {cap}{extra}"})
    always = policy["always_human"] if isinstance(policy.get("always_human"), dict) else DEFAULT_ALWAYS_HUMAN
    for cat, rule in always.items():
        rule = rule or {}
        hit = next((f"label {l}" for l in rule.get("labels") or [] if l.lower() in labels), None)
        if not hit:
            hit = next((f"path {p} (matches {g})" for p in paths
                        for g in [matches(p, rule.get("paths"))] if g), None)
        if not hit:
            scan = app_added if cat == "schema" else added
            hit = next((f"keyword \"{k}\" in an added line" for k in rule.get("keywords") or []
                        if k.lower() in scan), None)
        if hit:
            human.append({"category": cat, "why": hit})
    loss = next((k for k in MIGRATION_DATA_LOSS if k in mig_added), None)
    if loss and "schema" in always and not any(h["category"] == "schema" for h in human):
        human.append({"category": "schema", "why": f"keyword \"{loss.strip()}\" in a migration (outside down())"})

    run, needs = [], []
    if mig_files:
        allowed = mig.get("allowed_envs")
        allowed = DEFAULT_ALLOWED_ENVS if allowed is None else allowed
        base = cfg.get("base_branch") or "main"
        target = mig.get("target_env") or ("live" if base in ("main", "master") else "staging")
        cmds = mig.get("commands") or {}
        for env in allowed:
            cmd = (cmds.get(env) or "").strip()
            if cmd and cmd != "-":
                run.append({"env": env, "cmd": cmd})
            elif env == target and not cmd:
                needs.append(f"<your {env} migrate command>   # {env}: allowed, but migrations.commands.{env} is not set")
        if target not in allowed:
            cmd = (cmds.get(target) or "").strip()
            if cmd != "-":
                needs.append(f"{cmd or '<your ' + target + ' migrate command>'}   # {target}: the base deploys here and it is not in allowed_envs")
        needs += [str(s) for s in mig.get("human_steps") or []]
    for line in body.splitlines():
        m = re.match(r"^\s*(?:>\s*)?(?:[-*]\s*)?needs-you:\s*(.+?)\s*$", line, re.I)
        if m and m.group(1).lower() != "done":
            needs.append(m.group(1).strip("`"))

    print(json.dumps({"human": human, "migrations": mig_files, "run": run,
                      "needs_you": needs, "done": False}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
