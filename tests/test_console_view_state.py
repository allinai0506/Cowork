"""Acceptance tests for console view-state persistence across full page reloads."""

import importlib.machinery
import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_console():
    path = ROOT / "console" / "herdr_factory_console.py"
    spec = importlib.util.spec_from_loader(
        "herdr_console_view_state_test",
        importlib.machinery.SourceFileLoader("herdr_console_view_state_test", str(path)),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestViewStatePersistence(unittest.TestCase):
    """刷新页面必须停留在当前视图（空间 / Workflow / 运维驾驶舱），而不是回到首页。"""

    @classmethod
    def setUpClass(cls):
        cls.html = load_console().HTML

    def function_body(self, name):
        match = re.search(
            rf"(?:async )?function {name}\([^)]*\)\{{(.*?)\n\}}",
            self.html,
            re.DOTALL,
        )
        self.assertIsNotNone(match, f"missing JavaScript function: {name}")
        return match.group(1)

    def test_state_helpers_persist_full_view_context(self):
        save_body = self.function_body("saveViewState")
        self.assertIn("localStorage.setItem", save_body)
        for field in ("opsMode", "spaceId", "workflowId"):
            self.assertIn(field, save_body)
        load_body = self.function_body("loadViewState")
        self.assertIn("localStorage.getItem", load_body)

    def test_helpers_degrade_without_local_storage(self):
        # 隐私模式等 localStorage 不可用场景必须静默降级，不能阻塞启动。
        self.assertIn("try{", self.function_body("saveViewState"))
        self.assertIn("catch(e)", self.function_body("loadViewState"))

    def test_every_view_switch_persists_state(self):
        for fn in ("showOpsCenter", "exitOpsCenter", "openWorkflowFromOps",
                   "selectSpace", "loadWorkflow", "clearWorkflow"):
            self.assertIn("saveViewState()", self.function_body(fn),
                          f"{fn} mutates view state without persisting it")

    def test_boot_restores_saved_view(self):
        self.assertIn("loadViewState()", self.html)
        restore = re.search(r"\(function\(\)\{const v=loadViewState\(\);(.*?)\}\)\(\);", self.html, re.DOTALL)
        self.assertIsNotNone(restore, "missing boot-time view-state restore")
        for field in ("opsMode", "spaceId", "workflowId"):
            self.assertIn(field, restore.group(1))

    def test_boot_branches_between_ops_center_and_factory(self):
        self.assertIn("state.opsMode?showOpsCenter():refreshAll();", self.html)


if __name__ == "__main__":
    unittest.main()
