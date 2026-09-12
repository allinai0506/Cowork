import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Ensure herdr root is on sys.path
HERDR_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(HERDR_ROOT))

from herdr import projects
from herdr.projects import register_workflow, requirement_subject


class RequirementSubjectTest(unittest.TestCase):
    def test_plain_first_line(self):
        self.assertEqual(
            requirement_subject("实现用户登录功能\n第二步再接验证码"),
            "实现用户登录功能",
        )

    def test_strips_markdown_prefixes(self):
        self.assertEqual(requirement_subject("## 目标：重构 DAG 校验"), "目标：重构 DAG 校验")
        self.assertEqual(requirement_subject("- 添加导出按钮"), "添加导出按钮")
        self.assertEqual(requirement_subject("1. 修复分页 bug"), "修复分页 bug")
        self.assertEqual(requirement_subject("> 引用式需求"), "引用式需求")
        self.assertEqual(requirement_subject("- [ ] 打包产物检查"), "打包产物检查")

    def test_strips_inline_emphasis(self):
        self.assertEqual(
            requirement_subject("任务：X **一句话**：写一个 `命令`"),
            "任务：X 一句话：写一个 命令",
        )

    def test_keeps_leading_digits_in_words(self):
        self.assertEqual(requirement_subject("3D 打印预览"), "3D 打印预览")

    def test_skips_blank_lines(self):
        self.assertEqual(requirement_subject("\n\n  \n真正的需求"), "真正的需求")

    def test_prefers_body_over_generic_heading(self):
        self.assertEqual(
            requirement_subject("## 需求\n实现暗色模式切换\n细节待定"),
            "实现暗色模式切换",
        )

    def test_heading_only_document_uses_heading_text(self):
        self.assertEqual(
            requirement_subject("# 重构 DAG 校验器"),
            "重构 DAG 校验器",
        )

    def test_truncates_long_line(self):
        subject = requirement_subject("长" * 100)
        self.assertEqual(len(subject), 64)
        self.assertTrue(subject.endswith("…"))

    def test_empty_requirement(self):
        self.assertEqual(requirement_subject(""), "")
        self.assertEqual(requirement_subject(None), "")


class RegisterWorkflowSubjectTest(unittest.TestCase):
    def test_register_persists_subject(self):
        project = {
            "project_id": "demo",
            "project_name": "Demo",
            "project_root": "/tmp/demo",
            "workspace_id": "ws-1",
            "coordinator_pane_id": "pane-1",
            "workflow_file": "/tmp/demo-workflow.json",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                projects, "WORKFLOWS_FILE", Path(tmp) / "workflows.json"
            ):
                register_workflow(
                    "wf-demo-1", project, requirement="## 需求\n实现暗色模式切换\n细节待定"
                )
                record = projects.load_workflows()["workflows"]["wf-demo-1"]
        self.assertEqual(record["requirement_subject"], "实现暗色模式切换")


if __name__ == "__main__":
    unittest.main()
