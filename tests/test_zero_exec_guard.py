"""Zero-execution guard (controller) and herdr-task set --reason persistence tests."""

import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERDR_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERDR_ROOT))


def _load_module(name, path):
    spec = importlib.util.spec_from_loader(
        name,
        importlib.machinery.SourceFileLoader(name, str(path)),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ctl = _load_module(
    "herdr_controller_zero_exec_test",
    HERDR_ROOT / "services" / "herdr-controller.py",
)
_ht = _load_module(
    "herdr_task_zero_exec_test",
    HERDR_ROOT / "bin" / "herdr-task",
)


def _task(task_id="t1", status="working", working_at=None, started_at=None):
    history = []
    if working_at is not None:
        history.append(
            {
                "status": "working",
                "at": working_at,
                "from": "dispatched",
                "to": "working",
            }
        )

    task = {
        "task_id": task_id,
        "workflow_id": "wf-test",
        "stage": "wrapup",
        "pane_id": "wA:pZ",
        "agent": "qodercli",
        "status": status,
        "status_history": history,
    }

    if started_at is not None:
        task["started_at"] = started_at

    return task


class ControllerZeroExecGuard(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.events = []

        def fake_get_task(task_id):
            return self.state.get(task_id)

        def fake_set_task_status(task_id, status):
            self.state[task_id]["status"] = status
            return True

        def fake_enqueue(task, event_type):
            self.events.append((task["task_id"], event_type))

        for target, fake in (
            ("get_task", fake_get_task),
            ("set_task_status", fake_set_task_status),
            ("enqueue_coordinator_event", fake_enqueue),
        ):
            patcher = patch.object(_ctl, target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _register(self, task):
        self.state[task["task_id"]] = task
        return task

    def test_phantom_done_routes_to_blocked_and_zero_exec(self):
        task = self._register(_task(working_at=time.time() - 0.4))

        _ctl.handle_event("t1", "done")

        self.assertEqual(task["status"], "blocked")
        self.assertEqual(self.events, [("t1", "zero_exec")])

    def test_phantom_idle_routes_to_blocked_and_zero_exec(self):
        task = self._register(_task(working_at=time.time() - 0.4))

        _ctl.handle_event("t1", "idle")

        self.assertEqual(task["status"], "blocked")
        self.assertEqual(self.events, [("t1", "zero_exec")])

    def test_real_execution_enqueues_done(self):
        task = self._register(_task(working_at=time.time() - 30))

        _ctl.handle_event("t1", "done")

        self.assertEqual(task["status"], "agent_done")
        self.assertEqual(self.events, [("t1", "done")])

    def test_threshold_is_read_from_module_constant(self):
        task = self._register(_task(working_at=time.time() - 30))

        with patch.object(_ctl, "ZERO_EXEC_MIN_SECONDS", 60.0):
            _ctl.handle_event("t1", "done")

        self.assertEqual(task["status"], "blocked")
        self.assertEqual(self.events, [("t1", "zero_exec")])

    def test_recovery_phantom_agent_done_reroutes_to_zero_exec(self):
        task = self._register(_task(status="agent_done", working_at=time.time() - 0.4))

        _ctl.reconcile_task_state("t1")

        self.assertEqual(task["status"], "blocked")
        self.assertEqual(self.events, [("t1", "zero_exec")])

    def test_recovery_real_agent_done_restores_done_event(self):
        task = self._register(_task(status="agent_done", working_at=time.time() - 30))

        _ctl.reconcile_task_state("t1")

        self.assertEqual(task["status"], "agent_done")
        self.assertEqual(self.events, [("t1", "done")])

    def test_working_elapsed_seconds_fallbacks(self):
        now = 1000.0

        self.assertEqual(
            _ctl.working_elapsed_seconds(_task(working_at=now - 7), now=now),
            7.0,
        )
        self.assertAlmostEqual(
            _ctl.working_elapsed_seconds(_task(started_at=now - 9), now=now),
            9.0,
        )
        self.assertIsNone(_ctl.working_elapsed_seconds(_task(), now=now))
        self.assertIsNone(_ctl.working_elapsed_seconds(None, now=now))

    def test_zero_exec_message_carries_recovery_instructions(self):
        message = _ctl.build_coordinator_message(
            _task(working_at=time.time() - 0.4),
            "zero_exec",
        )

        self.assertIn("HERDR_CONTROLLER_ZERO_EXEC_EVENT", message)
        self.assertIn("禁止新建 Task", message)
        self.assertIn("completed / rework / failed", message)
        self.assertIn("wA:pZ", message)


class SetReasonPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="herdr-zeroexec-")
        self.tasks_file = Path(self.tmp.name) / "tasks.json"
        _ht.TASKS_FILE = str(self.tasks_file)

    def _write_registry(self, task):
        self.tasks_file.write_text(
            json.dumps({"tasks": [task]}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load_registry(self):
        return json.loads(self.tasks_file.read_text(encoding="utf-8"))["tasks"][0]

    def test_failed_with_reason_persists_failure_reason(self):
        self._write_registry(_task(status="agent_done"))
        reason = "评审阻断B1,未达收尾条件;建议修复后另起工作流"

        _ht.set_status("t1", "failed", reason)

        self.assertEqual(self._load_registry()["failure_reason"], reason)

    def test_failed_without_reason_leaves_no_field(self):
        self._write_registry(_task(status="agent_done"))

        _ht.set_status("t1", "failed")

        self.assertNotIn("failure_reason", self._load_registry())

    def test_reason_ignored_on_non_failed_status(self):
        self._write_registry(_task(status="agent_done"))

        _ht.set_status("t1", "completed", "误配的理由不应落盘")

        self.assertNotIn("failure_reason", self._load_registry())


if __name__ == "__main__":
    unittest.main()
