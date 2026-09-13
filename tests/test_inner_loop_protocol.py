"""Tests for Inner Loop Protocol (Task 1 — Phase 0).

Covers:
1. evaluator.generate_blocker_report: BLOCKER.md created when loop exhausted
2. evaluator.generate_blocker_report: contains required escalation fields
3. herdr-sentinel: HERDR_TASK_BLOCKER signal source-level verification
4. herdr-task: prompt iron-rule prohibitions source-level verification
"""

import sys
import unittest
from pathlib import Path

HERDR_ROOT = Path(__file__).resolve().parent.parent
if str(HERDR_ROOT) not in sys.path:
    sys.path.insert(0, str(HERDR_ROOT))


# ---------------------------------------------------------------------------
# 1 & 2: generate_blocker_report (via herdr.evaluator)
# ---------------------------------------------------------------------------

class BlockerReportGenerationTest(unittest.TestCase):
    """generate_blocker_report() must write a valid BLOCKER.md."""

    def setUp(self):
        from herdr.evaluator import MetricVector, generate_blocker_report, LOOP_DIR_NAME
        self.MetricVector = MetricVector
        self.generate_blocker_report = generate_blocker_report
        self.LOOP_DIR_NAME = LOOP_DIR_NAME

    def _make_loop_dir(self, tmp_path):
        loop_dir = Path(tmp_path) / self.LOOP_DIR_NAME
        loop_dir.mkdir(parents=True, exist_ok=True)
        return loop_dir

    def test_blocker_md_created_on_exhaustion(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            loop_dir = self._make_loop_dir(tmpdir)
            metrics = self.MetricVector(
                correctness=50.0,
                quality=80.0,
                scope=100.0,
                repro=0.0,
                composite_score=55.0,
                failing_tests=["test_foo", "test_bar"],
                lint_errors=2,
                has_repro_test=True,
            )
            result = self.generate_blocker_report(loop_dir, metrics, iteration=3, max_iter=3)
            self.assertTrue(result.exists(), "BLOCKER.md should be created")
            self.assertEqual(result.name, "BLOCKER.md")

    def test_blocker_md_contains_failing_tests(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            loop_dir = self._make_loop_dir(tmpdir)
            metrics = self.MetricVector(
                correctness=0.0,
                quality=70.0,
                scope=100.0,
                repro=0.0,
                composite_score=20.0,
                failing_tests=["tests/test_alpha.py::test_x", "tests/test_beta.py::test_y"],
                lint_errors=0,
                has_repro_test=True,
            )
            result = self.generate_blocker_report(loop_dir, metrics, iteration=5, max_iter=5)
            content = result.read_text(encoding="utf-8")
            self.assertIn("Escalation Blocker Report", content, "Must have report title")
            self.assertIn("test_alpha", content, "Must list failing test names")
            self.assertIn("test_beta", content, "Must list failing test names")
            self.assertIn("HERDR_TASK_BLOCKER", content, "Must mention escalation signal")
            self.assertIn("5 次重试", content, "Must state the max retry count")

    def test_blocker_md_repro_status_when_failing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            loop_dir = self._make_loop_dir(tmpdir)
            metrics = self.MetricVector(
                correctness=80.0,
                quality=100.0,
                scope=100.0,
                repro=0.0,
                composite_score=60.0,
                failing_tests=[],
                lint_errors=0,
                has_repro_test=True,
            )
            result = self.generate_blocker_report(loop_dir, metrics, iteration=3, max_iter=3)
            content = result.read_text(encoding="utf-8")
            self.assertIn("未通过", content, "Repro failure must be marked in BLOCKER.md")

    def test_blocker_md_repro_status_when_passing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            loop_dir = self._make_loop_dir(tmpdir)
            metrics = self.MetricVector(
                correctness=70.0,
                quality=80.0,
                scope=100.0,
                repro=100.0,
                composite_score=77.0,
                failing_tests=["test_z"],
                lint_errors=0,
                has_repro_test=False,
            )
            result = self.generate_blocker_report(loop_dir, metrics, iteration=3, max_iter=3)
            content = result.read_text(encoding="utf-8")
            self.assertIn("无复现用例", content, "No repro test must say '无复现用例'")

    def test_blocker_md_is_written_atomically(self):
        """BLOCKER.md must not leave a .md.tmp file behind."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            loop_dir = self._make_loop_dir(tmpdir)
            metrics = self.MetricVector(composite_score=10.0)
            self.generate_blocker_report(loop_dir, metrics, iteration=2, max_iter=2)
            tmp_files = list(loop_dir.glob("*.tmp"))
            self.assertEqual(tmp_files, [], "No .tmp files should remain after atomic write")


# ---------------------------------------------------------------------------
# 3: Sentinel BLOCKER signal detection (source-level verification)
# ---------------------------------------------------------------------------

class SentinelBlockerDetectionTest(unittest.TestCase):
    """Sentinel source must contain BLOCKER marker detection with correct status."""

    def _sentinel_source(self):
        return (HERDR_ROOT / "services" / "herdr-sentinel.py").read_text(encoding="utf-8")

    def test_blocker_marker_defined_in_sentinel(self):
        src = self._sentinel_source()
        self.assertIn("HERDR_TASK_BLOCKER:{task_id}", src,
                      "Sentinel must define blocker_marker variable")

    def test_blocker_transitions_to_blocked_not_agent_done(self):
        src = self._sentinel_source()
        idx = src.find("blocker_marker in screen")
        self.assertGreater(idx, 0, "Sentinel must check blocker_marker in screen")
        section = src[idx:idx + 300]
        self.assertIn('"blocked"', section, "Blocker must set status to 'blocked'")
        self.assertNotIn('"agent_done"', section, "Blocker must NOT set 'agent_done'")

    def test_blocker_reason_is_inner_loop_exhausted(self):
        src = self._sentinel_source()
        self.assertIn("inner_loop_exhausted", src,
                      "Blocked reason must be 'inner_loop_exhausted'")

    def test_sentinel_log_message_for_blocker(self):
        src = self._sentinel_source()
        self.assertIn("SENTINEL BLOCKER", src,
                      "Sentinel must print [SENTINEL BLOCKER] log on escalation")


# ---------------------------------------------------------------------------
# 4: Prompt iron-rule injection (source-level verification)
# ---------------------------------------------------------------------------

class PromptIronRuleInjectionTest(unittest.TestCase):
    """herdr-task dispatch_task() prompt must contain all inner loop iron rules."""

    def _herdr_task_source(self):
        return (HERDR_ROOT / "bin" / "herdr-task").read_text(encoding="utf-8")

    def test_prohibit_done_before_self_check(self):
        src = self._herdr_task_source()
        self.assertIn("严禁在自检未通过前输出完成标记", src)

    def test_prohibit_abandoning_workstation(self):
        src = self._herdr_task_source()
        self.assertIn("严禁在自检未通过时放弃工位", src)

    def test_prohibit_escalating_micro_issues(self):
        src = self._herdr_task_source()
        self.assertIn("严禁向上层汇报局部问题", src)

    def test_blocker_escalation_protocol_included(self):
        src = self._herdr_task_source()
        self.assertIn("HERDR_TASK_BLOCKER:", src)
        self.assertIn("熔断求助流程", src)

    def test_inner_loop_protocol_header(self):
        src = self._herdr_task_source()
        self.assertIn("Inner Loop Protocol", src)

    def test_normal_flow_steps_included(self):
        src = self._herdr_task_source()
        self.assertIn("GOAL.md", src)
        self.assertIn("herdr-loop eval", src)


if __name__ == "__main__":
    unittest.main()
