"""Regression tests for executor self-check accuracy (deep preflight).

Covers the 2026-09-15 report: "opencode ERROR code=1 / claude TIMEOUT 35s".
- claude cold start measured 36.9s success (occasionally >60s flake), so a
  flat 35s timeout deterministically misreports healthy-but-slow as TIMEOUT.
- Fast startup failures (e.g. 401/402/billing/overloaded phrasing) previously
  fell through to generic ERROR with no actionable classification.
"""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def load_deep_preflight():
    spec = importlib.util.spec_from_file_location(
        "herdr_deep_preflight_accuracy",
        str(ROOT / "herdr" / "deep_preflight.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_console_html():
    return (ROOT / "console" / "herdr_factory_console.py").read_text(encoding="utf-8")


class TestClassifyAccuracy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_deep_preflight()

    def test_billing_and_quota_variants_map_to_token_exhausted(self):
        for text in [
            "Premium request limit reached",
            "402 Payment Required",
            "Out of credits for this billing period",
            "credit balance depleted",
            "Free tier limit exhausted",
        ]:
            self.assertEqual(
                self.m.classify_text(text), "TOKEN_EXHAUSTED", msg=text
            )

    def test_auth_code_variants_map_to_auth_required(self):
        for text in [
            "401 Unauthorized",
            "403 Forbidden",
            "API key expired, please rotate",
            "access denied for this key",
            "Unauthenticated request",
        ]:
            self.assertEqual(
                self.m.classify_text(text), "AUTH_REQUIRED", msg=text
            )

    def test_transient_provider_issues_map_to_provider_error(self):
        for text in [
            "The model is overloaded, try again later",
            "Internal Server Error",
            "503 Service Unavailable",
            "model not found: muse-spark-xyz",
            "connection refused by provider",
        ]:
            self.assertEqual(
                self.m.classify_text(text), "PROVIDER_ERROR", msg=text
            )

    def test_benign_output_stays_unclassified(self):
        self.assertIsNone(self.m.classify_text("HERDR_PREFLIGHT_OK"))
        self.assertIsNone(self.m.classify_text(""))


class TestSmokeTimeoutAndRetry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_deep_preflight()

    def test_claude_gets_longer_timeout_than_default(self):
        self.assertGreater(
            self.m.SMOKE_TIMEOUTS.get("claude", 0),
            self.m.DEFAULT_SMOKE_TIMEOUT,
        )
        # 35s flat timeout misfired on a measured 36.9s healthy run.
        self.assertGreaterEqual(self.m.SMOKE_TIMEOUTS["claude"], 60)

    def test_claude_timeout_is_retried_once_then_reported(self):
        m = self.m

        def fake_run(cmd, timeout=12, cwd=None, stdin=None):
            class R:
                returncode = None
                stdout = ""
                stderr = ""

            raise __import__("subprocess").TimeoutExpired(cmd, timeout)

        with patch.object(m, "choose_smoke_command",
                          return_value=(["claude", "--print", "x"], "claude --print")), \
             patch.object(m, "run", side_effect=fake_run), \
             patch.object(m.time, "sleep", return_value=None):
            # normalize_result receives TimeoutExpired-raised dict path via run();
            # emulate run() returning timeout dict instead for determinism.
            def timeout_run(cmd, timeout=12, cwd=None, stdin=None):
                return {"timeout": True, "stdout": "", "stderr": "",
                        "returncode": None}

            with patch.object(m, "run", side_effect=timeout_run) as run_mock:
                res = m.smoke_probe("claude", "/usr/bin/claude", "/tmp")
        self.assertEqual(res["status"], "TIMEOUT")
        self.assertEqual(run_mock.call_count, 2)
        self.assertIn("重试", res["note"])

    def test_claude_retry_success_marks_ready(self):
        m = self.m
        calls = {"n": 0}

        def flaky_run(cmd, timeout=12, cwd=None, stdin=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"timeout": True, "stdout": "", "stderr": "",
                        "returncode": None}

            class R:
                returncode = 0
                stdout = "HERDR_PREFLIGHT_OK\n"
                stderr = ""

            return R()

        with patch.object(m, "choose_smoke_command",
                          return_value=(["claude", "--print", "x"], "claude --print")), \
             patch.object(m, "run", side_effect=flaky_run), \
             patch.object(m.time, "sleep", return_value=None):
            res = m.smoke_probe("claude", "/usr/bin/claude", "/tmp")
        self.assertEqual(res["status"], "READY")
        self.assertEqual(calls["n"], 2)

    def test_fast_startup_failure_keeps_evidence_output(self):
        m = self.m

        class R:
            returncode = 1
            stdout = ""
            stderr = "402 Payment Required: billing limit reached"

        with patch.object(m, "choose_smoke_command",
                          return_value=(["opencode", "run", "x"], "opencode run")), \
             patch.object(m, "run", return_value=R()):
            res = m.smoke_probe("opencode", "/usr/bin/opencode", "/tmp")
        # Must classify, not generic ERROR, and must keep evidence.
        self.assertEqual(res["status"], "TOKEN_EXHAUSTED")
        self.assertIn("402", res["output"])


class TestConsoleSelfCheckEvidence(unittest.TestCase):
    def test_modal_renders_probe_output_evidence(self):
        html = load_console_html()
        self.assertIn("deep.output", html)
        self.assertIn("PROVIDER_ERROR", html)
        self.assertIn("重试", html)


if __name__ == "__main__":
    unittest.main()
