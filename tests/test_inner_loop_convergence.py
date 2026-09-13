#!/opt/homebrew/bin/python3
"""Tests for Inner Loop Convergence and herdr-loop CLI lifecycle."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERDR_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERDR_ROOT))


class InnerLoopLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="herdr-inner-loop-")
        self.workdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_loop_init_and_convergence_progression(self):
        loop_bin = HERDR_ROOT / "bin" / "herdr-loop"

        # 1. Initialize loop in workdir
        init_res = subprocess.run(
            [
                str(loop_bin),
                "init",
                "--dir", str(self.workdir),
                "--goal", "实现多角色超管权限判定",
                "--acceptance", "- [ ] 所有单元测试通过\n- [ ] 靶向复现测试通过",
                "--test-cmd", "python3 -m unittest test_app.py",
                "--repro-cmd", "python3 repro_test.py",
                "--max-iter", "3",
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(init_res.returncode, 0)
        loop_dir = self.workdir / ".herdr-loop"
        self.assertTrue((loop_dir / "GOAL.md").exists())
        self.assertTrue((loop_dir / "STATE.md").exists())
        self.assertTrue((loop_dir / "METRICS.json").exists())
        self.assertTrue((loop_dir / "EVALUATOR.sh").exists())

        # Create a failing test and failing repro test
        (self.workdir / "app.py").write_text("""
def is_super_admin(roles):
    # Buggy initial implementation
    return "admin" in roles
""")
        (self.workdir / "test_app.py").write_text("""
import unittest
from app import is_super_admin

class AppTest(unittest.TestCase):
    def test_basic_admin(self):
        self.assertTrue(is_super_admin(["admin"]))
""")
        (self.workdir / "repro_test.py").write_text("""
import sys
from app import is_super_admin

# Fails on multi-role array with SUPER_ADMIN
roles = ["auditor", "SUPER_ADMIN"]
if not is_super_admin(roles):
    print("Repro failed: SUPER_ADMIN variant missed!")
    sys.exit(1)
print("Repro passed!")
""")

        # 2. First evaluation: test_app passes, but repro_test fails!
        eval_res1 = subprocess.run(
            [str(loop_bin), "eval", "--dir", str(self.workdir)],
            text=True,
            capture_output=True,
        )
        # Should exit 1 because repro test failed
        self.assertEqual(eval_res1.returncode, 1)

        # Check metrics and state
        metrics_data1 = json.loads((loop_dir / "METRICS.json").read_text())
        self.assertLess(metrics_data1["composite_score"], 100.0)
        self.assertEqual(metrics_data1["repro"], 0.0)
        self.assertIn("Repro failed", (loop_dir / "EVALUATION.md").read_text())

        # 3. Fix the bug in app.py (simulate agent's edit)
        (self.workdir / "app.py").write_text("""
def is_super_admin(roles):
    # Fixed implementation handling variations
    normalized = [r.upper() for r in roles]
    return "ADMIN" in normalized or "SUPER_ADMIN" in normalized
""")

        # 4. Second evaluation: should now pass and converge!
        eval_res2 = subprocess.run(
            [str(loop_bin), "eval", "--dir", str(self.workdir)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(eval_res2.returncode, 0)

        metrics_data2 = json.loads((loop_dir / "METRICS.json").read_text())
        self.assertEqual(metrics_data2["composite_score"], 100.0)
        self.assertEqual(metrics_data2["repro"], 100.0)
        
        state_text = (loop_dir / "STATE.md").read_text()
        self.assertIn("status**: converged", state_text)
        self.assertIn("converged**: true", state_text)


if __name__ == "__main__":
    unittest.main()
