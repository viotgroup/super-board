#!/usr/bin/env python3
"""super-board-merge-policy.py rules added from ECMS runs: rollback and test DROPs,
data loss in a forward migration, QA evidence and test files in the size count."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "scripts/super-board-merge-policy.py"
MIG = "src/database/migrations/1784000400000-AddTier.ts"


def diff_for(files):
    """files: {path: [added lines]} → a unified diff with one hunk per file."""
    out = []
    for path, lines in files.items():
        out += [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", f"@@ -0,0 +1,{len(lines)} @@"]
        out += ["+" + l for l in lines]
    return "\n".join(out) + "\n"


class MergePolicyRules(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def policy(self, files, config=None, sizes=None):
        cfg = {"base_branch": "main", "migrations": {"globs": ["src/database/migrations/*.ts"]}, **(config or {})}
        meta = {"labels": [], "body": "",
                "files": [{"path": p, "additions": (sizes or {}).get(p, len(ls)), "deletions": 0} for p, ls in files.items()]}
        (self.dir / "c.json").write_text(json.dumps(cfg))
        (self.dir / "m.json").write_text(json.dumps(meta))
        (self.dir / "d.diff").write_text(diff_for(files))
        out = subprocess.run(["python3", str(POLICY), "--config", str(self.dir / "c.json"), "--meta",
                              str(self.dir / "m.json"), "--diff", str(self.dir / "d.diff")],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def categories(self, result):
        return sorted({h["category"] for h in result["human"]})

    def test_drop_inside_a_rollback_is_not_destructive(self):
        up_and_down = ["export class AddTier {", "  public async up(q) {", '    await q.query("ALTER TABLE meter ADD tier int")',
                       "  }", "  public async down(q) {", '    await q.query("ALTER TABLE meter DROP COLUMN tier")', "  }", "}"]
        self.assertNotIn("schema", self.categories(self.policy({MIG: up_and_down})))

    def test_drop_inside_up_still_gates(self):
        lines = ["export class DropTier {", "  public async up(q) {", '    await q.query("ALTER TABLE meter DROP COLUMN tier")', "  }",
                 "  // down() restores it", "  public async down(q) {", '    await q.query("ALTER TABLE meter ADD tier int")', "  }", "}"]
        self.assertIn("schema", self.categories(self.policy({MIG: lines})))

    def test_alembic_downgrade_is_a_rollback_too(self):
        lines = ["def upgrade():", "    op.add_column('meter', sa.Column('tier', sa.Integer()))", "",
                 "def downgrade():", "    op.execute('ALTER TABLE meter DROP COLUMN tier')"]
        self.assertNotIn("schema", self.categories(self.policy({"alembic/versions/1_tier.py": lines},
                                                                {"migrations": {"globs": ["alembic/versions/**"]}})))

    def test_data_loss_without_drop_gates_a_forward_migration(self):
        for statement in ('DELETE FROM reading WHERE tier IS NULL', 'TRUNCATE reading'):
            with self.subTest(statement):
                lines = ["export class Purge {", "  public async up(q) {", f'    await q.query("{statement}")', "  }", "}"]
                self.assertIn("schema", self.categories(self.policy({MIG: lines})))

    def test_app_code_that_deletes_rows_is_not_schema(self):
        self.assertNotIn("schema", self.categories(self.policy({"src/readings/purge.service.ts": ['await q.query("DELETE FROM reading")']})))

    def test_teardown_in_tests_and_qa_evidence_is_not_schema(self):
        for path in ("src/readings/purge.spec.ts", "e2e/setup.ts", "docs/super-board/runs/issue-42-qa-v1/reset.sql"):
            with self.subTest(path):
                self.assertNotIn("schema", self.categories(self.policy({path: ["DROP TABLE reading_fixture;"]})))

    def test_category_gating_off_also_silences_migration_data_loss(self):
        lines = ["export class Purge {", "  public async up(q) {", '    await q.query("TRUNCATE reading")', "  }", "}"]
        self.assertEqual(self.categories(self.policy({MIG: lines}, {"merge_policy": {"always_human": {}}})), [])

    def test_qa_evidence_does_not_count_toward_the_size_cap(self):
        files = {"src/a.ts": ["x"], "docs/super-board/runs/issue-42-qa-v1/REPORT.md": ["y"]}
        result = self.policy(files, {"merge_policy": {"auto_max_lines": 100}}, {"src/a.ts": 50, "docs/super-board/runs/issue-42-qa-v1/REPORT.md": 160})
        self.assertNotIn("size", self.categories(result))

    def test_size_exclude_tests_is_opt_in(self):
        files = {"src/a.ts": ["x"], "src/a.spec.ts": ["y"]}
        sizes = {"src/a.ts": 60, "src/a.spec.ts": 300}
        self.assertIn("size", self.categories(self.policy(files, {"merge_policy": {"auto_max_lines": 100}}, sizes)))
        self.assertNotIn("size", self.categories(self.policy(files, {"merge_policy": {"auto_max_lines": 100, "size_exclude_tests": True}}, sizes)))
        sizes["src/a.ts"] = 160
        self.assertIn("size", self.categories(self.policy(files, {"merge_policy": {"auto_max_lines": 100, "size_exclude_tests": True}}, sizes)),
                      "app code over the cap still needs a human")


if __name__ == "__main__":
    unittest.main()
