#!/usr/bin/env python3
"""Project workflow switcher ordering: newest-first by created_at.

Regression context: workflow IDs mix two naming schemes
(`wf-proj-<hash>-<timestamp>` and `wf-proj-<MMDD>-<seq>`), so lexicographic
sorting put an older hashed ID above a newer date-sequenced one. The console
then defaulted to a stale workflow and buried the running one last in the
switcher — users reported "启动后看不到任务".
"""

from console import herdr_factory_console as c


def _wf(wid, created_at, project_id="p1", status="completed"):
    return {
        "workflow_id": wid,
        "project_id": project_id,
        "status": status,
        "created_at": created_at,
        "requirement_subject": wid,
    }


def _seed(monkeypatch):
    wfs = {
        "wf-proj-a380753e-20260912-155001": _wf("wf-proj-a380753e-20260912-155001", 100),
        "wf-proj-0914-02": _wf("wf-proj-0914-02", 200),
        "wf-proj-0915-01": _wf("wf-proj-0915-01", 300, status="running"),
        "wf-other-01": _wf("wf-other-01", 400, project_id="p2"),
    }
    monkeypatch.setattr(c, "workflows", lambda: wfs)
    return wfs


def test_workflows_for_project_sorted_by_created_at_desc(monkeypatch):
    _seed(monkeypatch)
    out = c.workflows_for_project("p1")
    assert [w["workflow_id"] for w in out] == [
        "wf-proj-0915-01",
        "wf-proj-0914-02",
        "wf-proj-a380753e-20260912-155001",
    ]


def test_project_detail_latest_workflow_is_newest_not_lexicographic(monkeypatch):
    _seed(monkeypatch)
    monkeypatch.setattr(c, "project_by_id", lambda pid: {"project_id": pid, "workspace_id": "w1"})
    monkeypatch.setattr(c, "tabs", lambda ws: [])
    monkeypatch.setattr(c, "panes", lambda ws: [])
    monkeypatch.setattr(c, "slots", lambda p: [])
    monkeypatch.setattr(c, "preflight", lambda p: [])

    detail = c.project_detail("p1")
    assert detail["latest_workflow_id"] == "wf-proj-0915-01"


def test_project_detail_handles_missing_created_at(monkeypatch):
    wfs = {
        "wf-legacy-no-ts": {"workflow_id": "wf-legacy-no-ts", "project_id": "p1", "status": "completed"},
        "wf-proj-0915-01": _wf("wf-proj-0915-01", 300, status="running"),
    }
    monkeypatch.setattr(c, "workflows", lambda: wfs)
    out = c.workflows_for_project("p1")
    assert [w["workflow_id"] for w in out] == ["wf-proj-0915-01", "wf-legacy-no-ts"]
