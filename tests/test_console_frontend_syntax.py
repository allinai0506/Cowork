"""Automated tests guarding console HTML/JS template syntax and UI contracts.

Prevents regressions where unescaped Python multi-line string interpolation
or invalid JS syntax crashes the frontend on page load.
"""

import importlib.machinery
import importlib.util
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_console():
    path = ROOT / "console" / "herdr_factory_console.py"
    spec = importlib.util.spec_from_loader(
        "herdr_console_frontend_test",
        importlib.machinery.SourceFileLoader("herdr_console_frontend_test", str(path)),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestConsoleFrontendSyntaxAndContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.console = _load_console()
        cls.html = getattr(cls.console, "HTML_TEMPLATE", "")

    def test_html_template_is_raw_string_in_source(self):
        """Guard against Python escape issues: HTML_TEMPLATE must be declared as raw string r''' or r\"\"\"."""
        source = (ROOT / "console" / "herdr_factory_console.py").read_text(encoding="utf-8")
        match = re.search(r"HTML_TEMPLATE\s*=\s*(r['\"]{3})", source)
        self.assertIsNotNone(
            match,
            "HTML_TEMPLATE must be declared with a raw string prefix r''' or r\"\"\" "
            "to prevent Python from mutating \\n in JS regexes and split strings.",
        )

    def test_javascript_syntax_clean_in_template(self):
        """Extract inline <script> block and validate with node -c to catch JS syntax crashes."""
        script_match = re.search(r"<script>(.*?)</script>", self.html, re.DOTALL)
        self.assertIsNotNone(script_match, "<script> block not found in HTML_TEMPLATE")
        js_code = script_match.group(1)

        # Locate node executable
        node_bin = shutil.which("node") or shutil.which("node", path="/Users/user/.volta/bin:/usr/local/bin:/opt/homebrew/bin")
        if not node_bin:
            self.skipTest("node executable not found in PATH; skipping JS syntax compilation test")

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js_code)
            temp_path = f.name

        try:
            res = subprocess.run([node_bin, "-c", temp_path], capture_output=True, text=True)
            self.assertEqual(
                res.returncode,
                0,
                f"JavaScript syntax error in HTML_TEMPLATE:\n{res.stderr}",
            )
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_new_workflow_modal_has_task_title_in_correct_order(self):
        """Verify modal form fields contract: 项目 -> 本次任务名称 -> 工作流模板 -> 执行者策略 -> 自然语言需求."""
        self.assertIn('id="newTitle"', self.html)
        self.assertIn("本次任务名称", self.html)
        self.assertIn("autoFillWorkflowTitle()", self.html)

        # Verify ordering of label texts in modal definition
        match_proj = re.search(r"<label[^>]*>项目</label>", self.html)
        match_title = re.search(r"<label[^>]*>本次任务名称</label>", self.html)
        match_tpl = re.search(r"<label[^>]*>工作流模板</label>", self.html)
        match_agent = re.search(r"<label[^>]*>执行者策略</label>", self.html)
        match_req = re.search(r"<label[^>]*>自然语言需求</label>", self.html)
        self.assertIsNotNone(match_proj, "missing <label>项目</label>")
        self.assertIsNotNone(match_title, "missing <label>本次任务名称</label>")
        self.assertIsNotNone(match_tpl, "missing <label>工作流模板</label>")
        self.assertIsNotNone(match_agent, "missing <label>执行者策略</label>")
        self.assertIsNotNone(match_req, "missing <label>自然语言需求</label>")

        p_proj = match_proj.start()
        p_title = match_title.start()
        p_tpl = match_tpl.start()
        p_agent = match_agent.start()
        p_req = match_req.start()

        self.assertTrue(
            -1 < p_proj < p_title < p_tpl < p_agent < p_req,
            f"Modal field order violated: proj={p_proj}, title={p_title}, tpl={p_tpl}, agent={p_agent}, req={p_req}",
        )

    def test_auto_fill_workflow_title_logic_present(self):
        """Ensure autoFillWorkflowTitle function is present and avoids generic headers."""
        self.assertIn("function autoFillWorkflowTitle()", self.html)
        self.assertIn("## 需求", self.html)
        self.assertIn("onblur=\"autoFillWorkflowTitle()\"", self.html)

    def test_modal_and_toast_accessibility_attributes(self):
        """Ensure modal dialog and toast have proper WCAG ARIA attributes."""
        self.assertIn('role="dialog"', self.html)
        self.assertIn('aria-modal="true"', self.html)
        self.assertIn('aria-labelledby="modalTitle"', self.html)
        self.assertIn('role="alert"', self.html)

    def test_keyboard_escape_closes_modal(self):
        """Verify Escape key listener is registered to close modal and dropdowns."""
        self.assertIn("Escape", self.html)
        self.assertIn("closeModal()", self.html)

    def test_native_blocking_dialogs_eliminated(self):
        """Ensure blocking native confirm() and prompt() calls are replaced by styled modals."""
        # Find script block
        script_match = re.search(r"<script>(.*?)</script>", self.html, re.DOTALL)
        self.assertIsNotNone(script_match)
        js = script_match.group(1)
        # Should not have naked confirm( or prompt( calls in JS
        self.assertNotIn("confirm(", js)
        self.assertNotIn("prompt(", js)
        self.assertIn("showConfirmModal", js)
        self.assertIn("showPromptModal", js)

    def test_primary_button_styling_and_no_duplicate_plus(self):
        """Ensure primary button uses white text on royal blue and avoids double plus icons."""
        # Check no duplicate plus in buttons
        self.assertNotIn("＋ 新需求", self.html)
        self.assertNotIn("＋ 新建模板", self.html)
        self.assertIn("<span>新需求</span>", self.html)
        # Ensure primary button has white text and not muddy black text
        self.assertNotIn(".btn.primary{background:var(--accent);color:#06111f;", self.html)
        self.assertIn(".btn.primary{background:#2563eb;color:#ffffff;", self.html)


if __name__ == "__main__":
    unittest.main()

