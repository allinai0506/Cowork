"""
Regression tests for the workflow deadlock fix.

Covers:
  1. TRANSITIONS: superseded is reachable from failed/cleaned/in-progress.
  2. is_node_complete: superseded tasks excluded; node unblocks correctly.
  3. reconcile_stage_advance_states: notified lock revoked on predecessor regress.
  4. supersede_task: marks task superseded with correct metadata.
  5. stage_reset / force_advance: clear stage-state.json keys correctly.
"""

import importlib
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERDR_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERDR_ROOT))


def _import_herdr_task():
    task_bin = HERDR_ROOT / "bin" / "herdr-task"
    spec = importlib.util.spec_from_loader(
        "herdr_task_bin",
        importlib.machinery.SourceFileLoader("herdr_task_bin", str(task_bin)),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ht = _import_herdr_task()
TRANSITIONS = _ht.TRANSITIONS


def _stub_herdr_modules():
    for name in ("herdr.projects", "herdr.workflow", "herdr_projects", "herdr_workflow"):
        if name not in sys.modules:
            stub = types.ModuleType(name)
            stub.project_for_workflow = lambda wf: {}
            stub.workflow_config_for = lambda wf: None
            stub.find_node = lambda cfg, n: None
            stub.get_ready_nodes = lambda cfg, done: []
            stub.is_workflow_completed = lambda cfg, done: False
            stub.normalize_workflow = lambda cfg: cfg
            sys.modules[name] = stub


def _load_controller(unique_name):
    _stub_herdr_modules()
    ctrl_path = HERDR_ROOT / "services" / "herdr-controller.py"
    spec = importlib.util.spec_from_loader(
        unique_name,
        importlib.machinery.SourceFileLoader(unique_name, str(ctrl_path)),
    )
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pass
    return mod


# ---------------------------------------------------------------------------
# 1. TRANSITIONS
# ---------------------------------------------------------------------------

class TestTransitions(unittest.TestCase):

    def test_superseded_from_failed(self):
        self.assertIn("superseded", TRANSITIONS["failed"])

    def test_superseded_from_cleaned(self):
        self.assertIn("superseded", TRANSITIONS["cleaned"])

    def test_superseded_from_in_progress(self):
        for state in ("dispatched", "working", "blocked", "agent_done", "rework"):
            with self.subTest(state=state):
                self.assertIn("superseded", TRANSITIONS[state])

    def test_superseded_is_terminal(self):
        self.assertEqual(TRANSITIONS["superseded"], set())

    def test_pending_cannot_supersede(self):
        self.assertNotIn("superseded", TRANSITIONS.get("pending", set()))

    def test_completed_chain_unaffected(self):
        for state in ("completed", "committed", "integrated", "cleanup_ready"):
            with self.subTest(state=state):
                self.assertNotIn("superseded", TRANSITIONS.get(state, set()))


# ---------------------------------------------------------------------------
# 2. is_node_complete
# ---------------------------------------------------------------------------

class TestIsNodeComplete(unittest.TestCase):

    def setUp(self):
        self.ctrl = _load_controller("ctrl_is_node_complete")

    def _set_tasks(self, tasks):
        self.ctrl.load_tasks = lambda: tasks

    def test_failed_blocks_node(self):
        self._set_tasks([
            {"task_id": "t1", "workflow_id": "wf1", "node": "fix", "stage": "fix",
             "status": "failed"},
        ])
        self.assertFalse(self.ctrl.is_node_complete("wf1", "fix"))

    def test_superseded_plus_cleaned_replacement_completes_node(self):
        self._set_tasks([
            {"task_id": "t1", "workflow_id": "wf1", "node": "fix", "stage": "fix",
             "status": "superseded", "superseded_by": "t2"},
            {"task_id": "t2", "workflow_id": "wf1", "node": "fix", "stage": "fix",
             "status": "cleaned"},
        ])
        self.assertTrue(self.ctrl.is_node_complete("wf1", "fix"))

    def test_all_superseded_no_replacement_is_incomplete(self):
        self._set_tasks([
            {"task_id": "t1", "workflow_id": "wf1", "node": "fix", "stage": "fix",
             "status": "superseded"},
        ])
        self.assertFalse(self.ctrl.is_node_complete("wf1", "fix"))

    def test_cleaned_plus_superseded_completes_node(self):
        self._set_tasks([
            {"task_id": "t1", "workflow_id": "wf1", "node": "fix", "stage": "fix",
             "status": "cleaned"},
            {"task_id": "t2", "workflow_id": "wf1", "node": "fix", "stage": "fix",
             "status": "superseded"},
        ])
        self.assertTrue(self.ctrl.is_node_complete("wf1", "fix"))


# ---------------------------------------------------------------------------
# 3. reconcile_stage_advance_states
# ---------------------------------------------------------------------------

class TestReconcile(unittest.TestCase):

    def setUp(self):
        self.ctrl = _load_controller("ctrl_reconcile")

    def test_notified_revoked_when_predecessor_regresses(self):
        wf = "wf-reconcile-1"
        workflow_cfg = {
            "nodes": [
                {"id": "fix", "depends_on": []},
                {"id": "test", "depends_on": ["fix"]},
            ]
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({f"{wf}:test": "notified"}, f)
            tmp = f.name
        try:
            self.ctrl.STAGE_STATE_FILE = tmp
            self.ctrl.load_tasks = lambda: [
                {"task_id": "t1", "workflow_id": wf, "node": "fix",
                 "stage": "fix", "status": "failed"},
            ]
            self.ctrl.reconcile_stage_advance_states(wf, workflow_cfg)
            with open(tmp) as f:
                state = json.load(f)
            self.assertNotIn(f"{wf}:test", state)
        finally:
            os.unlink(tmp)

    def test_notified_preserved_when_predecessor_complete(self):
        wf = "wf-reconcile-2"
        workflow_cfg = {
            "nodes": [
                {"id": "fix", "depends_on": []},
                {"id": "test", "depends_on": ["fix"]},
            ]
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({f"{wf}:test": "notified"}, f)
            tmp = f.name
        try:
            self.ctrl.STAGE_STATE_FILE = tmp
            self.ctrl.load_tasks = lambda: [
                {"task_id": "t1", "workflow_id": wf, "node": "fix",
                 "stage": "fix", "status": "cleaned"},
            ]
            self.ctrl.reconcile_stage_advance_states(wf, workflow_cfg)
            with open(tmp) as f:
                state = json.load(f)
            self.assertEqual(state.get(f"{wf}:test"), "notified")
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 4. supersede_task
# ---------------------------------------------------------------------------

class TestSupersedeTask(unittest.TestCase):

    def _store(self, tasks):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"tasks": tasks}, f)
            return f.name

    def test_supersede_failed_task(self):
        path = self._store([{"task_id": "old-1", "status": "failed"}])
        try:
            _ht.TASKS_FILE = path
            _ht.supersede_task("old-1", new_task_id="new-1", reason="retry")
            with open(path) as f:
                data = json.load(f)
            t = next(x for x in data["tasks"] if x["task_id"] == "old-1")
            self.assertEqual(t["status"], "superseded")
            self.assertEqual(t["superseded_by"], "new-1")
            self.assertEqual(t["supersede_reason"], "retry")
        finally:
            os.unlink(path)

    def test_supersede_cleaned_task(self):
        path = self._store([{"task_id": "old-2", "status": "cleaned"}])
        try:
            _ht.TASKS_FILE = path
            _ht.supersede_task("old-2")
            with open(path) as f:
                data = json.load(f)
            t = next(x for x in data["tasks"] if x["task_id"] == "old-2")
            self.assertEqual(t["status"], "superseded")
        finally:
            os.unlink(path)

    def test_supersede_pending_rejected(self):
        path = self._store([{"task_id": "p-1", "status": "pending"}])
        try:
            _ht.TASKS_FILE = path
            with self.assertRaises(SystemExit) as cm:
                _ht.supersede_task("p-1")
            self.assertEqual(cm.exception.code, 2)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 5. stage_reset / force_advance
# ---------------------------------------------------------------------------

class TestStageReset(unittest.TestCase):

    def _store(self, data):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(data, f)
            return f.name

    def test_stage_reset_clears_all_for_workflow(self):
        path = self._store({
            "wf-a:test": "notified",
            "wf-a:review": "queued",
            "wf-b:test": "notified",
        })
        try:
            with patch("os.path.expanduser", return_value=path):
                _ht.stage_reset("wf-a")
            with open(path) as f:
                state = json.load(f)
            self.assertNotIn("wf-a:test", state)
            self.assertNotIn("wf-a:review", state)
            self.assertIn("wf-b:test", state)
        finally:
            os.unlink(path)

    def test_stage_reset_single_stage(self):
        path = self._store({
            "wf-a:test": "notified",
            "wf-a:review": "queued",
        })
        try:
            with patch("os.path.expanduser", return_value=path):
                _ht.stage_reset("wf-a", "test")
            with open(path) as f:
                state = json.load(f)
            self.assertNotIn("wf-a:test", state)
            self.assertIn("wf-a:review", state)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 6. ops-center cards vs controller parity
# ---------------------------------------------------------------------------

class TestOpsCardParity(unittest.TestCase):
    """Ops-center node cards must agree with is_node_complete on superseded
    tasks — regression for the 2026-09-12 stats drift (cards counted
    superseded tasks in the denominator while is_node_complete excluded them).
    """

    def test_card_counts_match_is_node_complete(self):
        ctrl = _load_controller("ctrl_ops_parity")
        tasks = [
            {"task_id": "t1", "workflow_id": "wf1", "node": "fix", "stage": "fix",
             "status": "superseded", "superseded_by": "t2"},
            {"task_id": "t2", "workflow_id": "wf1", "node": "fix", "stage": "fix",
             "status": "cleaned"},
        ]
        ctrl.load_tasks = lambda: tasks

        self.assertTrue(ctrl.is_node_complete("wf1", "fix"))
        counts = _ht._node_task_status_counts(tasks)
        self.assertEqual(counts["total"], counts["completed"])
        self.assertEqual(counts["total"], 1)
        self.assertEqual(counts["superseded"], 1)


if __name__ == "__main__":
    unittest.main()
